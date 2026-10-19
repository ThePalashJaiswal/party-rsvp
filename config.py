"""
=====================================================================
  EVENT SETTINGS — the one obvious place to change party details.
=====================================================================

How settings are resolved (highest priority first):

  1. The "Config" worksheet in your Google Sheet (edited from the host
     dashboard → Settings tab, or directly in the sheet). Any NON-EMPTY
     value there overrides the default below.
  2. The defaults in this file.

Any value that still starts with "REPLACE_WITH" is treated as NOT SET:
it is hidden from guests and never used as live data. In particular the
app refuses to accept RSVPs until RSVP_DEADLINE is a real date-time
(unless the host explicitly forces registration open).

Secrets (Google credentials, admin password, token pepper) NEVER go here
or in the sheet — they live in Streamlit secrets. See README.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import rsvp_logic as L

# ---------------------------------------------------------------------
# Fixed facts (do not change unless the party moves)
# ---------------------------------------------------------------------
EVENT_DATE = date(2026, 10, 31)          # Saturday, 31 October 2026 (Halloween)
TIMEZONE = "Asia/Kolkata"                # all times below are IST

# ---------------------------------------------------------------------
# Event details — fill these in (or set them from the host dashboard)
# ---------------------------------------------------------------------
EVENT_TITLE = "Halloween Costume Party"        # matches the invite
HOST_NAMES = "REPLACE_WITH_HOST_NAMES"         # e.g. "Palash & Rashika"
VENUE = "House No. 555, Shivashray, Shiva Property, Mendori"                # full address or a friendly name
VENUE_MAP_URL = ""                             # optional https:// Google Maps link
START_TIME = "18:00"                           # 24h "HH:MM" — 6 PM onwards
END_TIME = ""                                  # optional 24h "HH:MM"
DRESS_CODE = "Dress up in your spookiest, or in hues of the Halloween night sky."
HOST_CONTACT = ""                              # shown when RSVPs are closed, e.g. "WhatsApp Palash on 98xxxxxxx"

# RSVP deadline — format "YYYY-MM-DD HH:MM", interpreted in Asia/Kolkata.
# Must be in the future and before the party starts.
RSVP_DEADLINE = "REPLACE_WITH_FINAL_DATE_AND_TIME"   # e.g. "2026-10-28 23:59"

# ---------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------
MAX_PARTY_SIZE = 4                 # max people per RSVP, including the responder (1–20)
REGISTRATION_MODE = "auto"         # "auto" = follow deadline | "open" = force open | "closed" = force closed
ALLOW_EDITS_AFTER_DEADLINE = False # can guests change an RSVP once registration closes?

# ---------------------------------------------------------------------
# Look & copy
# ---------------------------------------------------------------------
HERO_IMAGE_URL = ""                # https:// image URL. Otherwise hero.jpg in the repo is used
HEADLINE = "Get your freak on."
APP_URL = ""                       # your public Streamlit URL, used to build guests' private edit links

# ---------------------------------------------------------------------
# Internals — no need to edit below this line
# ---------------------------------------------------------------------
DEFAULTS: dict[str, str] = {
    "event_title": EVENT_TITLE,
    "host_names": HOST_NAMES,
    "venue": VENUE,
    "venue_map_url": VENUE_MAP_URL,
    "start_time": START_TIME,
    "end_time": END_TIME,
    "dress_code": DRESS_CODE,
    "host_contact": HOST_CONTACT,
    "rsvp_deadline": RSVP_DEADLINE,
    "max_party_size": str(MAX_PARTY_SIZE),
    "registration_mode": REGISTRATION_MODE,
    "allow_edits_after_deadline": "true" if ALLOW_EDITS_AFTER_DEADLINE else "false",
    "hero_image_url": HERO_IMAGE_URL,
    "headline": HEADLINE,
    "app_url": APP_URL,
}

SETTING_NOTES: dict[str, str] = {
    "event_title": "Party name shown at the top of the invite",
    "host_names": "Who is hosting",
    "venue": "Address or place name",
    "venue_map_url": "Optional https:// map link",
    "start_time": "24h HH:MM, IST",
    "end_time": "Optional 24h HH:MM, IST",
    "dress_code": "Optional",
    "host_contact": "How guests reach you once RSVPs close",
    "rsvp_deadline": "YYYY-MM-DD HH:MM, IST",
    "max_party_size": "People per RSVP incl. responder, 1-20",
    "registration_mode": "auto | open | closed",
    "allow_edits_after_deadline": "true | false",
    "hero_image_url": "Optional https:// image",
    "headline": "Big line on the welcome screen",
    "app_url": "Public app URL, for private edit links",
}

MAX_PARTY_LIMIT = 20


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in {"true", "yes", "1", "on", "y"}


@dataclass(frozen=True)
class Settings:
    event_title: str
    host_names: str
    venue: str
    venue_map_url: str
    start_time: str
    end_time: str
    dress_code: str
    host_contact: str
    rsvp_deadline: str
    max_party_size: int
    registration_mode: str
    allow_edits_after_deadline: bool
    hero_image_url: str
    headline: str
    app_url: str
    event_date: date = EVENT_DATE
    sources: tuple = ()  # keys that came from the Config sheet

    def shown(self, field: str) -> str:
        """Value safe to show guests ('' if unset/placeholder)."""
        v = getattr(self, field)
        return "" if L.is_placeholder(v) else str(v).strip()


def merge_raw(overrides: dict | None) -> tuple[dict[str, str], set[str]]:
    raw = dict(DEFAULTS)
    from_sheet: set[str] = set()
    for k, v in (overrides or {}).items():
        if k in raw and str(v).strip() != "":
            raw[k] = str(v).strip()
            from_sheet.add(k)
    return raw, from_sheet


def load_settings(overrides: dict | None = None) -> Settings:
    raw, from_sheet = merge_raw(overrides)
    try:
        mps = int(float(raw["max_party_size"]))
    except (TypeError, ValueError):
        mps = MAX_PARTY_SIZE
    mps = max(1, min(MAX_PARTY_LIMIT, mps))
    mode = raw["registration_mode"].strip().lower()
    if mode not in L.REGISTRATION_MODES:
        mode = "auto"
    return Settings(
        event_title=raw["event_title"],
        host_names=raw["host_names"],
        venue=raw["venue"],
        venue_map_url=raw["venue_map_url"],
        start_time=raw["start_time"],
        end_time=raw["end_time"],
        dress_code=raw["dress_code"],
        host_contact=raw["host_contact"],
        rsvp_deadline=raw["rsvp_deadline"],
        max_party_size=mps,
        registration_mode=mode,
        allow_edits_after_deadline=_truthy(raw["allow_edits_after_deadline"]),
        hero_image_url=raw["hero_image_url"],
        headline=raw["headline"],
        app_url=raw["app_url"].rstrip("/"),
        sources=tuple(sorted(from_sheet)),
    )


def gate_for(settings: Settings, now=None) -> "L.Gate":
    return L.registration_gate(
        now=now or L.now_local(),
        deadline_str=settings.rsvp_deadline,
        event_date=settings.event_date,
        start_time=settings.start_time,
        mode=settings.registration_mode,
        allow_edits_after_deadline=settings.allow_edits_after_deadline,
    )
