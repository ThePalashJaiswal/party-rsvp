"""Shared Streamlit plumbing: secrets, storage, settings, styling."""

from __future__ import annotations

import base64
import json
from html import escape
from pathlib import Path

import streamlit as st

import config
import rsvp_logic as L
from google_sheets import SheetsStore, StorageError, open_spreadsheet

ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"


# ---------------------------------------------------------------------
# Secrets (never shown to guests)
# ---------------------------------------------------------------------
def secret(section: str, key: str, default: str = "") -> str:
    try:
        return str(st.secrets[section][key])
    except Exception:  # noqa: BLE001 — missing file/section/key all mean "not set"
        return default


def token_pepper() -> str:
    return secret("app", "token_pepper")


def admin_password() -> str:
    return secret("admin", "password")


def storage_mode() -> str:
    return secret("app", "storage", "sheets").strip().lower()


def _service_account_json() -> str:
    try:
        return json.dumps(dict(st.secrets["gcp_service_account"]), sort_keys=True)
    except Exception:  # noqa: BLE001
        return ""


@st.cache_resource(show_spinner=False)
def _build_store(mode: str, key: str, sa_json: str) -> SheetsStore:
    if mode == "memory":
        from sheet_fakes import FakeSpreadsheet
        store = SheetsStore(FakeSpreadsheet())
    else:
        store = SheetsStore(open_spreadsheet(json.loads(sa_json), key))
    store.ensure_schema()
    return store


def setup_problems() -> list[str]:
    """Host-facing list of missing configuration (no secret values)."""
    probs = []
    if not token_pepper() or len(token_pepper()) < 16:
        probs.append("`[app] token_pepper` is missing or shorter than 16 characters.")
    if not admin_password():
        probs.append("`[admin] password` is missing.")
    if storage_mode() != "memory":
        if not secret("app", "spreadsheet_key"):
            probs.append("`[app] spreadsheet_key` is missing.")
        if not _service_account_json():
            probs.append("`[gcp_service_account]` section is missing.")
    return probs


def get_store() -> SheetsStore | None:
    """Returns the store, or None if setup is incomplete / unreachable."""
    if storage_mode() != "memory" and (not secret("app", "spreadsheet_key") or not _service_account_json()):
        return None
    try:
        return _build_store(storage_mode(), secret("app", "spreadsheet_key"), _service_account_json())
    except Exception:  # noqa: BLE001 — details deliberately not shown
        return None


def store_error_detail() -> str:
    """For the host only: why the store could not be built (no secrets)."""
    try:
        _build_store(storage_mode(), secret("app", "spreadsheet_key"), _service_account_json())
        return ""
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "PermissionError" in name or "403" in str(exc):
            return "Google returned 'permission denied'. Share the spreadsheet with the service-account email as Editor."
        if "SpreadsheetNotFound" in name or "404" in str(exc):
            return "Spreadsheet not found. Check `[app] spreadsheet_key` and that it is shared with the service account."
        return f"Could not connect to Google Sheets ({name}). Check the service-account secrets."


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def _cached_overrides(_store: SheetsStore, mode: str) -> dict:
    return _store.read_config()


def current_settings(store: SheetsStore | None, fresh: bool = False) -> config.Settings:
    """`fresh=True` bypasses the cache and RAISES on failure — used for the
    server-side gate check at submit time so a stale cache can never let a
    late RSVP through."""
    if store is None:
        if fresh:
            raise StorageError("Storage is not configured.")
        return config.load_settings()
    if fresh:
        return config.load_settings(store.read_config())
    try:
        return config.load_settings(_cached_overrides(store, storage_mode()))
    except StorageError:
        return config.load_settings()


def clear_settings_cache() -> None:
    _cached_overrides.clear()


# ---------------------------------------------------------------------
# Visual system
# ---------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _css() -> str:
    return (ROOT / "styles.css").read_text(encoding="utf-8")


def inject_css() -> None:
    st.html(f"<style>{_css()}</style>")


def svg_img(svg: str, cls: str = "", alt: str = "") -> str:
    """st.html sanitises inline <svg>, so SVG art is embedded as a data-URI image."""
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg.strip().encode()).decode()
    role = f'alt="{escape(alt, quote=True)}"' if alt else 'alt="" aria-hidden="true"'
    return f'<img class="{cls}" src="{uri}" {role}>'


BAT_PATH = ("M50 20 C48 15 46 13 44 15 C42 10 35 7 28 9 C31 13 29 18 22 18 C17 16 9 17 1 25 C10 24 14 28 16 35 "
            "C20 30 26 30 30 35 C33 30 39 29 43 33 C45 29 47 28 50 31 C53 28 55 29 57 33 C61 29 67 30 70 35 "
            "C74 30 80 30 84 35 C86 28 90 24 99 25 C91 17 83 16 78 18 C71 18 69 13 72 9 C65 7 58 10 56 15 "
            "C54 13 52 15 50 20Z")


def _bat_svg(fill: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40"><path fill="{fill}" d="{BAT_PATH}"/>'
            f'<path fill="{fill}" d="M46 16 L45 9 L49 14 L51 14 L55 9 L54 16Z"/></svg>')


_WEB = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" fill="none" stroke="#F3E9DC" '
        'stroke-width="1.2" stroke-linecap="round"><path d="M200 0 L60 200 M200 0 L0 140 M200 0 L0 40 M200 0 L130 200"/>'
        '<path d="M170 0 Q178 24 200 30 M135 0 Q150 48 200 62 M95 0 Q118 78 200 100 M55 0 Q85 112 200 142"/>'
        '<path d="M200 30 Q186 40 186 52 M200 62 Q170 76 168 102 M200 100 Q150 118 146 150"/></svg>')


_SPARKLE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><path fill="#AB0000" '
            'd="M20 0 C21.2 12 28 18.8 40 20 C28 21.2 21.2 28 20 40 C18.8 28 12 21.2 0 20 C12 18.8 18.8 12 20 0Z"/></svg>')
_DOT = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path fill="#AB0000" d="M5 0 L6 4 L10 5 L6 6 L5 10 L4 6 L0 5 L4 4Z"/></svg>'


def _decor_html() -> str:
    sp = lambda cls: svg_img(_SPARKLE, f"spark {cls}")
    dt = lambda cls: svg_img(_DOT, f"spark {cls}")
    return ('<div class="bg-decor" aria-hidden="true">'
            + sp("s-tl1") + sp("s-tl2") + dt("s-tl3") + dt("s-tl4")
            + sp("s-br1") + sp("s-br2") + dt("s-br3") + dt("s-br4")
            + '</div>')


def decor() -> None:
    st.html(_decor_html())


def _bats_group(items) -> str:
    out = []
    for x, y, sc in items:
        out.append(f'<g transform="translate({x} {y}) scale({sc})"><path d="{BAT_PATH}"/>'
                   f'<path d="M46 16 L45 9 L49 14 L51 14 L55 9 L54 16Z"/></g>')
    return "".join(out)


HERO_FALLBACK_SVG = f"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 300" preserveAspectRatio="xMidYMid slice">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1B0E24"/><stop offset=".38" stop-color="#4A1D4F"/>
      <stop offset=".68" stop-color="#B8432B"/><stop offset=".86" stop-color="#F2731D"/><stop offset="1" stop-color="#F6C453"/>
    </linearGradient>
    <radialGradient id="sun" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="#FFE7A3"/><stop offset=".7" stop-color="#F9B248"/><stop offset="1" stop-color="#F2731D"/>
    </radialGradient>
    <clipPath id="sunclip"><rect x="0" y="0" width="600" height="236"/></clipPath>
  </defs>
  <rect width="600" height="300" fill="url(#sky)"/>
  <g fill="#F3E9DC" opacity=".7"><circle cx="60" cy="30" r="1.2"/><circle cx="140" cy="18" r="1"/><circle cx="420" cy="26" r="1.3"/>
    <circle cx="520" cy="48" r="1"/><circle cx="250" cy="40" r="0.9"/><circle cx="560" cy="14" r="1.1"/></g>
  <g clip-path="url(#sunclip)">
    <circle cx="330" cy="236" r="84" fill="url(#sun)"/>
    <rect x="240" y="196" width="180" height="5" fill="#C7542A" opacity=".55"/>
    <rect x="240" y="212" width="180" height="7" fill="#C7542A" opacity=".55"/>
    <rect x="240" y="226" width="180" height="9" fill="#C7542A" opacity=".55"/>
  </g>
  <path d="M0 232 C 90 214, 170 238, 260 228 C 360 216, 450 240, 600 222 L600 300 L0 300Z" fill="#120A18"/>
  <g stroke="#120A18" stroke-linecap="round" fill="none">
    <path d="M92 300 C 96 260, 98 220, 104 182" stroke-width="13"/>
    <path d="M101 214 C 82 196, 66 190, 46 192" stroke-width="6"/>
    <path d="M70 193 C 62 180, 60 170, 64 160" stroke-width="3.5"/>
    <path d="M103 196 C 116 172, 136 162, 160 160" stroke-width="6"/>
    <path d="M140 162 C 146 150, 156 144, 168 144" stroke-width="3.5"/>
    <path d="M104 184 C 100 160, 104 140, 116 124" stroke-width="5"/>
    <path d="M110 140 C 98 128, 88 126, 78 128" stroke-width="3"/>
    <path d="M150 161 C 158 168, 164 178, 166 188" stroke-width="2.5"/>
  </g>
  <g fill="#120A18">{_bats_group([(150, 70, .42), (205, 46, .3), (452, 64, .36), (500, 96, .24), (390, 38, .22)])}</g>
  <g transform="translate(468 236)">
    <ellipse cx="0" cy="18" rx="36" ry="28" fill="#E0611A"/><ellipse cx="-14" cy="18" rx="18" ry="27" fill="#F2731D"/>
    <ellipse cx="14" cy="18" rx="18" ry="27" fill="#F2731D"/><ellipse cx="0" cy="18" rx="12" ry="28" fill="#F58A2E"/>
    <path d="M-3 -9 C-2 -16 2 -20 7 -21 L8 -17 C4 -16 3 -12 3 -8Z" fill="#4E6B2E"/>
    <path d="M-20 10 L-10 2 L-6 14Z M20 10 L10 2 L6 14Z" fill="#FFD36B"/>
    <path d="M-22 26 L-14 32 L-8 26 L-2 33 L4 26 L10 33 L16 26 L22 28 C14 42 -14 42 -22 26Z" fill="#FFD36B"/>
  </g>
  <g transform="translate(528 258) scale(.6)">
    <ellipse cx="0" cy="18" rx="36" ry="28" fill="#D9581A"/><ellipse cx="0" cy="18" rx="14" ry="28" fill="#EE6F22"/>
    <path d="M-3 -9 C-2 -16 2 -20 7 -21 L8 -17 C4 -16 3 -12 3 -8Z" fill="#4E6B2E"/>
  </g>
</svg>
"""

JACK_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="-50 -32 100 92">
  <ellipse cx="0" cy="18" rx="44" ry="36" fill="#E0611A"/><ellipse cx="-17" cy="18" rx="22" ry="35" fill="#F2731D"/>
  <ellipse cx="17" cy="18" rx="22" ry="35" fill="#F2731D"/><ellipse cx="0" cy="18" rx="15" ry="36" fill="#F58A2E"/>
  <path d="M-4 -16 C-3 -24 2 -29 9 -30 L10 -25 C5 -24 4 -19 4 -15Z" fill="#4E6B2E"/>
  <path d="M-26 8 L-12 -2 L-7 14Z M26 8 L12 -2 L7 14Z M0 16 L-5 24 L5 24Z" fill="#2A1238"/>
  <path d="M-28 30 L-18 37 L-10 30 L-3 38 L4 30 L11 38 L19 30 L28 32 C18 50 -18 50 -28 30Z" fill="#2A1238"/>
</svg>
"""


def _find_image(stem: str) -> Path | None:
    for folder in (ASSETS, ROOT):
        for ext in ("jpg", "jpeg", "png", "webp"):
            p = folder / f"{stem}.{ext}"
            if p.exists() and p.stat().st_size < 6_000_000:
                return p
    return None


@st.cache_data(show_spinner=False)
def _image_data_uri(stem: str) -> str:
    p = _find_image(stem)
    if not p:
        return ""
    mime = {"jpg": "jpeg"}.get(p.suffix[1:].lower(), p.suffix[1:].lower())
    return f"data:image/{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _local_hero_data_uri() -> str:
    return _image_data_uri("hero")


def hero_html(settings: config.Settings) -> str:
    url = settings.shown("hero_image_url")
    src = url if url.startswith("https://") else _local_hero_data_uri()
    if src:
        return f'<div class="hero"><img src="{escape(src, quote=True)}" alt="Three vintage skeletons from the party invitation"></div>'
    return f'<div class="hero">{svg_img(HERO_FALLBACK_SVG, "hero-svg", "Halloween sunset with bats and a jack-o-lantern")}</div>'


def toast_html() -> str:
    """The toasting skeleton from the invite; falls back to a jack-o-lantern."""
    src = _image_data_uri("toast")
    if src:
        return f'<img class="toast" src="{src}" alt="" aria-hidden="true">'
    return svg_img(JACK_SVG, "jack")


def progress_html(steps: list[str], current: str, labels: dict[str, str]) -> str:
    idx = steps.index(current) if current in steps else 0
    dots = []
    for i, s in enumerate(steps):
        cls = "done" if i < idx else ("current" if i == idx else "")
        dots.append(f'<li class="{cls}"><span class="dot"></span><span class="lbl">{escape(labels[s])}</span></li>')
    return (f'<nav class="progress" aria-label="RSVP progress: step {idx + 1} of {len(steps)}">'
            f'<ol>{"".join(dots)}</ol></nav>')


def confetti_html() -> str:
    colors = ["#AB0000", "#7A0000", "#F2F0EE", "#C93A3A", "#AB0000", "#4A0D0D"]
    pieces = []
    for i in range(26):
        left = (i * 37) % 100
        delay = (i % 7) * 0.18
        dur = 2.6 + (i % 5) * 0.35
        rot = (i * 47) % 360
        shape = "star" if i % 3 == 0 else ("bone" if i % 3 == 1 else "dot")
        bg = f";background:{colors[i % len(colors)]}"
        pieces.append(
            f'<span class="piece {shape}" style="left:{left}%;animation-delay:{delay:.2f}s;'
            f'animation-duration:{dur:.2f}s;--r:{rot}deg{bg}"></span>'
        )
    return f'<div class="confetti" aria-hidden="true">{"".join(pieces)}</div>'


def e(s) -> str:
    """HTML-escape user content before it goes into st.html."""
    return escape("" if s is None else str(s))


def chip(icon_svg: str, text: str) -> str:
    return f'<span class="chip">{icon_svg}<span>{e(text)}</span></span>'


ICON_CAL = svg_img('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#AB0000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 2v3M17 2v3M3 9h18M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z"/></svg>', "icon")
ICON_CLOCK = svg_img('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#AB0000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>', "icon")
ICON_PIN = svg_img('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#AB0000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21Z"/><circle cx="12" cy="9.5" r="2.5"/></svg>', "icon")
ICON_SHIRT = svg_img('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#AB0000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3 3 6l2 4 2-1v12h10V9l2 1 2-4-5-3a4 4 0 0 1-8 0Z"/></svg>', "icon")
ICON_HAT = svg_img('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#AB0000" d="M13 1.5C10.6 5.6 9.4 10 8.4 16h7.4c-.3-3.6-.1-6.6 2.6-8.8-2.6.3-4.4-1.3-5.4-5.7Z"/><ellipse cx="12" cy="17.6" rx="10.5" ry="2.7" fill="#AB0000"/><rect x="8.5" y="13.4" width="7.2" height="1.7" fill="#D9D9D9"/></svg>', "icon")
