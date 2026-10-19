"""
In-memory stand-in for a gspread Spreadsheet. Used by the unit tests and by
the optional local demo mode (`storage = "memory"` in secrets). Starts EMPTY:
no fabricated guests. Never use memory mode in production — data vanishes
when the app restarts.
"""

from __future__ import annotations

import re

try:
    from gspread.exceptions import WorksheetNotFound
except Exception:  # pragma: no cover
    class WorksheetNotFound(LookupError):
        pass


class FakeAPIError(Exception):
    def __init__(self, code: int):
        super().__init__(f"fake API error {code}")
        self.code = code


class _Cell:
    def __init__(self, value):
        self.value = value


def _col_num(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


class FakeWorksheet:
    def __init__(self, title: str):
        self.title = title
        self.rows: list[list[str]] = []
        self.fail_queue: list[tuple[str, Exception]] = []  # (method, exc) injected failures
        self.calls: list[str] = []

    def _maybe_fail(self, method: str):
        self.calls.append(method)
        for i, (m, exc) in enumerate(self.fail_queue):
            if m == method:
                self.fail_queue.pop(i)
                raise exc

    def _ensure(self, r: int, c: int):
        while len(self.rows) < r:
            self.rows.append([])
        row = self.rows[r - 1]
        while len(row) < c:
            row.append("")

    def get_all_values(self):
        self._maybe_fail("get_all_values")
        width = max((len(r) for r in self.rows), default=0)
        return [list(r) + [""] * (width - len(r)) for r in self.rows]

    def row_values(self, i: int):
        self._maybe_fail("row_values")
        if i > len(self.rows):
            return []
        r = list(self.rows[i - 1])
        while r and r[-1] == "":
            r.pop()
        return r

    def col_values(self, c: int):
        self._maybe_fail("col_values")
        out = [(r[c - 1] if len(r) >= c else "") for r in self.rows]
        while out and out[-1] == "":
            out.pop()
        return out

    def cell(self, r: int, c: int):
        return _Cell(self.rows[r - 1][c - 1] if r <= len(self.rows) and c <= len(self.rows[r - 1]) else "")

    def update(self, range_name=None, values=None, value_input_option=None, **_):
        self._maybe_fail("update")
        m = re.match(r"^([A-Z]+)(\d+)", range_name)
        c0, r0 = _col_num(m.group(1)), int(m.group(2))
        for dr, row in enumerate(values):
            self._ensure(r0 + dr, c0 + len(row) - 1)
            for dc, v in enumerate(row):
                self.rows[r0 + dr - 1][c0 + dc - 1] = str(v)

    def append_row(self, values, value_input_option=None, insert_data_option=None, table_range=None, **_):
        self._maybe_fail("append_row")
        self.rows.append([str(v) for v in values])

    def delete_rows(self, i: int):
        del self.rows[i - 1]


class FakeSpreadsheet:
    def __init__(self):
        self._sheets: dict[str, FakeWorksheet] = {}

    def worksheet(self, name: str):
        if name not in self._sheets:
            raise WorksheetNotFound(name)
        return self._sheets[name]

    def add_worksheet(self, title: str, rows: int = 100, cols: int = 26, **_):
        ws = FakeWorksheet(title)
        self._sheets[title] = ws
        return ws

    def worksheets(self):
        return list(self._sheets.values())
