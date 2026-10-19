"""
Google Sheets storage. Google Sheets is the source of truth.

Design notes (honest about the limits of a spreadsheet as a database):
  * Rows are located by the stable `rsvp_id` column, never by row number,
    and columns are matched by header NAME, so hosts can sort/reorder.
  * New rows use the Sheets "append" API, which Google applies atomically
    server-side, so two guests submitting at the same moment do not
    overwrite each other.
  * Writes are idempotent: an RSVP is looked up by id before it is written.
    If a write times out and is retried (or a guest double-taps), the retry
    updates the same row instead of adding a duplicate.
  * There are NO multi-row transactions or row locks. If a host deletes or
    re-sorts rows in the sheet at the exact moment a guest is saving an
    edit, that edit can land on the wrong row; we re-check the id just
    before writing to make this window tiny, but it is not zero. Ask hosts
    to avoid sorting the RSVPs tab in place (use a filter view instead).
"""

from __future__ import annotations

import random
import time
from typing import Callable

import rsvp_logic as L

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
WS_RSVPS = "RSVPs"
WS_CONFIG = "Config"
WS_INVITEES = "Invitees"
CONFIG_HEADERS = ["key", "value", "notes"]
TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


class StorageError(Exception):
    """Safe-to-display storage failure (never contains credentials)."""


class SchemaError(StorageError):
    pass


def open_spreadsheet(service_account_info: dict, spreadsheet_key: str):
    import gspread  # imported lazily so unit tests don't need network libs

    client = gspread.service_account_from_dict(service_account_info, scopes=SCOPES)
    return client.open_by_key(spreadsheet_key)


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    if code in TRANSIENT_CODES:
        return True
    name = type(exc).__name__
    return name in {"ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "TransportError"}


def _not_found_type():
    try:
        from gspread.exceptions import WorksheetNotFound
        return WorksheetNotFound
    except Exception:  # pragma: no cover
        return LookupError


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class SheetsStore:
    def __init__(self, spreadsheet, sleep: Callable[[float], None] = time.sleep, attempts: int = 4):
        self.ss = spreadsheet
        self.sleep = sleep
        self.attempts = attempts
        self._ws_cache: dict[str, object] = {}
        self._headers: list[str] | None = None

    # -- plumbing -------------------------------------------------------
    def _call(self, fn, *args, **kwargs):
        """Call a READ/idempotent API with exponential backoff on transient errors."""
        for i in range(self.attempts):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if _is_transient(exc) and i < self.attempts - 1:
                    self.sleep(0.6 * (2 ** i) + random.random() * 0.3)
                    continue
                if isinstance(exc, StorageError):
                    raise
                raise StorageError("We couldn't reach the guest list just now. Please try again in a moment.") from exc

    def _worksheet(self, name: str, headers: list[str] | None = None, create: bool = True):
        if name in self._ws_cache:
            return self._ws_cache[name]
        try:
            ws = self._call(self.ss.worksheet, name)
        except StorageError as exc:
            if not isinstance(exc.__cause__, _not_found_type()):
                raise
            if not create:
                return None
            ws = self._call(self.ss.add_worksheet, title=name, rows=200, cols=max(len(headers or []), 3))
            if headers:
                self._call(ws.update, range_name=f"A1:{_col_letter(len(headers))}1", values=[headers],
                           value_input_option="RAW")
        self._ws_cache[name] = ws
        return ws

    def _rsvp_ws(self):
        return self._worksheet(WS_RSVPS, L.RSVP_HEADERS)

    def headers(self, refresh: bool = False) -> list[str]:
        if self._headers is not None and not refresh:
            return self._headers
        ws = self._rsvp_ws()
        current = [h.strip() for h in self._call(ws.row_values, 1)]
        if not any(current):
            current = list(L.RSVP_HEADERS)
            self._call(ws.update, range_name=f"A1:{_col_letter(len(current))}1", values=[current],
                       value_input_option="RAW")
        missing = [h for h in L.RSVP_HEADERS if h not in current]
        if missing:
            # Additive migration: append any new columns at the end.
            start = len(current) + 1
            end = start + len(missing) - 1
            self._call(ws.update, range_name=f"{_col_letter(start)}1:{_col_letter(end)}1",
                       values=[missing], value_input_option="RAW")
            current = current + missing
        if len(set(h for h in current if h)) != len([h for h in current if h]):
            raise SchemaError("The RSVPs sheet has duplicate column headers. Please fix row 1.")
        self._headers = current
        return current

    def _col(self, name: str) -> int:
        return self.headers().index(name) + 1

    def ensure_schema(self) -> None:
        self.headers(refresh=True)
        cfg = self._worksheet(WS_CONFIG, CONFIG_HEADERS)
        rows = self._call(cfg.get_all_values)
        if not rows or not any(rows[0]):
            self._call(cfg.update, range_name="A1:C1", values=[CONFIG_HEADERS], value_input_option="RAW")

    # -- RSVP reads -----------------------------------------------------
    def _row_dict(self, values: list[str]) -> dict:
        hs = self.headers()
        values = list(values) + [""] * (len(hs) - len(values))
        return {h: values[i] for i, h in enumerate(hs) if h}

    def _find_row(self, column: str, value: str) -> int | None:
        if not value:
            return None
        ws = self._rsvp_ws()
        col = self._call(ws.col_values, self._col(column))
        for i, v in enumerate(col[1:], start=2):
            if v.strip() == value:
                return i
        return None

    def get_by_id(self, rsvp_id: str) -> dict | None:
        idx = self._find_row("rsvp_id", rsvp_id)
        if not idx:
            return None
        return self._row_dict(self._call(self._rsvp_ws().row_values, idx))

    def get_by_token(self, token: str, pepper: str) -> dict | None:
        """Look up a guest's own RSVP. Only the hash column is scanned; the
        matching row is then re-verified in constant time."""
        token_hash = L.hash_edit_token(token, pepper)
        idx = self._find_row("edit_token_hash", token_hash)
        if not idx:
            return None
        row = self._row_dict(self._call(self._rsvp_ws().row_values, idx))
        if not L.token_matches(token, row.get("edit_token_hash", ""), pepper):
            return None
        return row

    def list_rsvps(self) -> list[dict]:
        """Full read — host dashboard only, never the guest interface."""
        rows = self._call(self._rsvp_ws().get_all_values)
        if not rows:
            return []
        self._headers = None
        hs = self.headers()
        out = []
        for values in rows[1:]:
            if not any(v.strip() for v in values):
                continue
            values = list(values) + [""] * (len(hs) - len(values))
            r = {h: values[i] for i, h in enumerate(hs) if h}
            if r.get("rsvp_id"):
                out.append(r)
        return out

    # -- RSVP writes ----------------------------------------------------
    def upsert(self, record: dict) -> str:
        """Create or update the row whose rsvp_id matches. Columns missing
        from `record` keep their current values. Returns 'created'|'updated'."""
        if not record.get("rsvp_id"):
            raise ValueError("record needs an rsvp_id")
        last_exc: Exception | None = None
        for i in range(self.attempts):
            try:
                return self._upsert_once(record)
            except StorageError:
                raise  # reads inside already retried with backoff
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if i < self.attempts - 1 and _is_transient(exc):
                    self.sleep(0.6 * (2 ** i) + random.random() * 0.3)
                    continue
                raise StorageError("We couldn't save your RSVP just now. Please try again.") from exc
        raise StorageError("We couldn't save your RSVP just now. Please try again.") from last_exc

    def _upsert_once(self, record: dict) -> str:
        ws = self._rsvp_ws()
        hs = self.headers()
        rid = record["rsvp_id"]
        idx = self._find_row("rsvp_id", rid)
        if idx:
            existing = self._row_dict(ws.row_values(idx))
            if existing.get("rsvp_id", "").strip() != rid:  # rows moved under us
                idx = self._find_row("rsvp_id", rid)
                existing = self._row_dict(ws.row_values(idx)) if idx else None
        else:
            existing = None
        merged = dict(existing or {})
        merged.update({k: ("" if v is None else str(v)) for k, v in record.items()})
        values = [merged.get(h, "") if h else "" for h in hs]
        if idx:
            ws.update(range_name=f"A{idx}:{_col_letter(len(hs))}{idx}", values=[values],
                      value_input_option="RAW")
            return "updated"
        # Not wrapped in the inner retry: an append is not idempotent. The
        # outer loop in upsert() re-checks for the id before any retry.
        ws.append_row(values, value_input_option="RAW", insert_data_option="INSERT_ROWS",
                      table_range="A1")
        return "created"

    # -- Config & invitees ---------------------------------------------
    def read_config(self) -> dict[str, str]:
        ws = self._worksheet(WS_CONFIG, CONFIG_HEADERS)
        rows = self._call(ws.get_all_values)
        out = {}
        for row in rows[1:]:
            if row and row[0].strip():
                out[row[0].strip()] = row[1].strip() if len(row) > 1 else ""
        return out

    def write_config(self, updates: dict[str, str], notes: dict[str, str] | None = None) -> None:
        ws = self._worksheet(WS_CONFIG, CONFIG_HEADERS)
        rows = self._call(ws.get_all_values)
        if not rows:
            rows = [CONFIG_HEADERS]
            self._call(ws.update, range_name="A1:C1", values=[CONFIG_HEADERS], value_input_option="RAW")
        index = {r[0].strip(): i + 1 for i, r in enumerate(rows) if r and r[0].strip() and i > 0}
        for k, v in updates.items():
            note = (notes or {}).get(k, "")
            if k in index:
                self._call(ws.update, range_name=f"B{index[k]}", values=[[str(v)]], value_input_option="RAW")
            else:
                self._call(ws.append_row, [k, str(v), note], value_input_option="RAW",
                           insert_data_option="INSERT_ROWS", table_range="A1")

    def list_invitees(self) -> list[str] | None:
        """Optional 'Invitees' tab: names in column A (header in row 1)."""
        ws = self._worksheet(WS_INVITEES, create=False)
        if ws is None:
            return None
        col = self._call(ws.col_values, 1)
        return [c.strip() for c in col[1:] if c.strip()]
