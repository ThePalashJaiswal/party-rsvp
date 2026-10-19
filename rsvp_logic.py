"""
Pure business logic: validation, deadline rules, edit tokens, aggregation,
calendar files and CSV export. No Streamlit or Google imports, so it is
fully unit-testable.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------
ATTENDING = "Attending"
TENTATIVE = "Not Sure Yet"
DECLINED = "Unable to Attend"
STATUSES = [ATTENDING, TENTATIVE, DECLINED]

FOOD_VEG = "Vegetarian"
FOOD_NONVEG = "Non-vegetarian"
FOOD_VEGAN = "Vegan"
FOOD_OTHER = "Other"
FOOD_UNSURE = "Not sure yet"
FOOD_OPTIONS = [FOOD_VEG, FOOD_NONVEG, FOOD_VEGAN, FOOD_OTHER, FOOD_UNSURE]
FOOD_UNKNOWN = "Unknown (not given)"

ALCOHOL_OPTIONS = ["Beer", "Vodka", "Whiskey", "Rum", "Gin", "Wine", "Cocktails", "Other alcoholic"]
NO_ALCOHOL = "No alcohol"
ALCOHOL_CHOICES = ALCOHOL_OPTIONS + [NO_ALCOHOL]
NON_ALCOHOL_OPTIONS = ["Mocktails", "Soft drinks", "Juices", "Sparkling water", "Other non-alcoholic"]
SPIRITS = {"Vodka", "Whiskey", "Rum", "Gin"}

REGISTRATION_MODES = ("auto", "open", "closed")

MAX_NAME = 80
MAX_EMAIL = 120
MAX_PHONE = 20
MAX_SHORT = 200
MAX_SPECIAL = 500

PLACEHOLDER_PREFIX = "REPLACE_WITH"
DEADLINE_FORMAT = "%Y-%m-%d %H:%M"

DRINK_TEXT_FIELDS = {
    "beer_pref": "Beer style / brand",
    "spirits_pref": "Spirits",
    "wine_pref": "Wine",
    "cocktail_pref": "Cocktails",
    "mixer_pref": "Mixers",
    "brand_pref": "Brands",
    "other_drinks": "Anything else to stock",
}

# Column order for a NEW sheet. Existing sheets are matched by header NAME,
# so reordering columns in Google Sheets is safe.
RSVP_HEADERS = [
    "rsvp_id", "submitted_at", "updated_at", "guest_name", "email", "phone",
    "status", "party_size", "additional_guests", "attendees_json", "food_summary",
    "dietary_restrictions", "allergy_notes", "preferred_dishes",
    "alcohol_prefs", "non_alcohol_prefs",
    "beer_pref", "spirits_pref", "wine_pref", "cocktail_pref", "mixer_pref",
    "brand_pref", "other_drinks", "special_requests",
    "edit_token_hash", "edit_count", "last_edited_by", "host_notes",
]
PRIVATE_COLUMNS = {"edit_token_hash"}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^[+\d\s\-().]{7,20}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{24,64}$")


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------
def now_local() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def is_placeholder(v) -> bool:
    s = "" if v is None else str(v).strip()
    return s == "" or s.upper().startswith(PLACEHOLDER_PREFIX)


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def to_int(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


def split_list(v) -> list[str]:
    if isinstance(v, list):
        return [_s(x) for x in v if _s(x)]
    return [p.strip() for p in _s(v).split(",") if p.strip()]


def _dedupe(seq, allowed) -> list[str]:
    out = []
    for x in seq:
        if x in allowed and x not in out:
            out.append(x)
    return out


def normalize_name(name: str) -> str:
    s = re.sub(r"[^\w\s]", " ", _s(name).casefold())
    return re.sub(r"\s+", " ", s).strip()


def attendee_label(i: int, a: dict, guest_name: str = "") -> str:
    if i == 0:
        return _s(guest_name) or _s(a.get("name")) or "Guest 1"
    return _s(a.get("name")) or f"Guest {i + 1}"


# ---------------------------------------------------------------------
# Drafts (transient UI state) and records (what goes in the sheet)
# ---------------------------------------------------------------------
def blank_attendee(name: str = "") -> dict:
    return {"name": name, "food": "", "food_other": "", "allergy": ""}


def new_draft(status: str | None = None) -> dict:
    return {
        "rsvp_id": uuid.uuid4().hex,
        "guest_name": "", "email": "", "phone": "",
        "status": status if status in STATUSES else ATTENDING,
        "party_size": 1,
        "attendees": [blank_attendee()],
        "dietary_restrictions": "", "preferred_dishes": "",
        "alcohol_prefs": [], "non_alcohol_prefs": [],
        **{k: "" for k in DRINK_TEXT_FIELDS},
        "special_requests": "",
        "submitted_at": "",
        "edit_count": 0,
    }


def resize_attendees(d: dict) -> dict:
    """Make the attendee list match party_size; attendee 0 is the responder."""
    size = max(1, to_int(d.get("party_size"), 1))
    atts = [dict(blank_attendee(), **a) for a in (d.get("attendees") or [])]
    while len(atts) < size:
        atts.append(blank_attendee())
    d["attendees"] = atts[:size]
    d["attendees"][0]["name"] = _s(d.get("guest_name"))
    return d


def clean_draft(d: dict) -> dict:
    c = dict(d)
    for k in ("guest_name", "email", "phone", "dietary_restrictions", "preferred_dishes",
              "special_requests", *DRINK_TEXT_FIELDS):
        c[k] = _s(c.get(k))
    c["guest_name"] = re.sub(r"\s+", " ", c["guest_name"])
    c["email"] = c["email"].lower()
    c["status"] = c.get("status") if c.get("status") in STATUSES else ""
    c["party_size"] = to_int(c.get("party_size"), 0)
    c["alcohol_prefs"] = _dedupe(split_list(c.get("alcohol_prefs")), ALCOHOL_CHOICES)
    c["non_alcohol_prefs"] = _dedupe(split_list(c.get("non_alcohol_prefs")), NON_ALCOHOL_OPTIONS)
    atts = []
    for a in c.get("attendees") or []:
        atts.append({
            "name": re.sub(r"\s+", " ", _s(a.get("name"))),
            "food": _s(a.get("food")) if _s(a.get("food")) in FOOD_OPTIONS else "",
            "food_other": _s(a.get("food_other")),
            "allergy": _s(a.get("allergy")),
        })
    c["attendees"] = atts or [blank_attendee()]
    c["attendees"][0]["name"] = c["guest_name"]
    for a in c["attendees"]:
        if a["food"] != FOOD_OTHER:
            a["food_other"] = ""
    return c


# ---------------------------------------------------------------------
# Validation — returns a list of human-readable problems ([] = valid)
# ---------------------------------------------------------------------
def validate_guest_info(d: dict, max_party: int) -> list[str]:
    c = clean_draft(d)
    errs = []
    if len(c["guest_name"]) < 2:
        errs.append("Please tell us your full name.")
    elif len(c["guest_name"]) > MAX_NAME:
        errs.append(f"Your name must be {MAX_NAME} characters or fewer.")
    if c["status"] not in STATUSES:
        errs.append("Please choose whether you can make it.")
    if c["status"] in (ATTENDING, TENTATIVE):
        raw = d.get("party_size")
        if not isinstance(raw, int) and not str(raw).strip().lstrip("-").isdigit():
            errs.append("Number of guests must be a whole number.")
        elif c["party_size"] < 1:
            errs.append("Your party must include at least you (1 person).")
        elif c["party_size"] > max_party:
            errs.append(f"Each RSVP can include up to {max_party} people. Please reach out to the hosts for larger groups.")
        for i, a in enumerate(c["attendees"][1:], start=2):
            if len(a["name"]) > MAX_NAME:
                errs.append(f"Guest {i}'s name must be {MAX_NAME} characters or fewer.")
    if c["email"]:
        if len(c["email"]) > MAX_EMAIL or not EMAIL_RE.match(c["email"]):
            errs.append("That email address doesn't look right. It's optional, so you can leave it blank.")
    if c["phone"]:
        digits = re.sub(r"\D", "", c["phone"])
        if not PHONE_RE.match(c["phone"]) or not 7 <= len(digits) <= 15:
            errs.append("That mobile number doesn't look right. It's optional, so you can leave it blank.")
    return errs


def validate_food(d: dict) -> list[str]:
    c = clean_draft(d)
    if c["status"] == DECLINED:
        return []
    errs = []
    for i, a in enumerate(c["attendees"]):
        label = attendee_label(i, a, c["guest_name"])
        if c["status"] == ATTENDING and not a["food"]:
            errs.append(f"Pick a food preference for {label} (\"Not sure yet\" is fine).")
        if len(a["food_other"]) > MAX_SHORT:
            errs.append(f"{label}'s dietary note is too long ({MAX_SHORT} characters max).")
        if len(a["allergy"]) > MAX_SHORT:
            errs.append(f"{label}'s allergy note is too long ({MAX_SHORT} characters max).")
    for k, label in (("dietary_restrictions", "Dietary restrictions"), ("preferred_dishes", "Preferred dishes")):
        if len(c[k]) > MAX_SHORT:
            errs.append(f"{label} is too long ({MAX_SHORT} characters max).")
    return errs


def validate_drinks(d: dict) -> list[str]:
    c = clean_draft(d)
    if c["status"] == DECLINED:
        return []
    errs = []
    if NO_ALCOHOL in c["alcohol_prefs"] and len(c["alcohol_prefs"]) > 1:
        errs.append("You've picked \"No alcohol\" and an alcoholic drink. Please choose one or the other.")
    for k, label in DRINK_TEXT_FIELDS.items():
        if len(c[k]) > MAX_SHORT:
            errs.append(f"{label} is too long ({MAX_SHORT} characters max).")
    return errs


def validate_requests(d: dict) -> list[str]:
    if len(_s(d.get("special_requests"))) > MAX_SPECIAL:
        return [f"Special requests can be up to {MAX_SPECIAL} characters."]
    return []


def validate_all(d: dict, max_party: int) -> list[str]:
    return validate_guest_info(d, max_party) + validate_food(d) + validate_drinks(d) + validate_requests(d)


def record_from_draft(d: dict) -> dict:
    """Guest-owned columns for the sheet. Keys absent here (host_notes,
    submitted_at, edit_token_hash...) are preserved by the store on update."""
    c = clean_draft(d)
    coming = c["status"] != DECLINED
    atts = c["attendees"][: c["party_size"]] if coming else []
    rec = {
        "rsvp_id": c["rsvp_id"],
        "guest_name": c["guest_name"],
        "email": c["email"],
        "phone": c["phone"],
        "status": c["status"],
        "party_size": str(c["party_size"]) if coming else "0",
        "additional_guests": ", ".join(attendee_label(i, a) for i, a in enumerate(atts) if i > 0),
        "attendees_json": json.dumps(atts, ensure_ascii=False) if coming else "",
        "food_summary": " | ".join(
            f"{attendee_label(i, a, c['guest_name'])}: {food_display(a)}" for i, a in enumerate(atts)
        ),
        "dietary_restrictions": c["dietary_restrictions"] if coming else "",
        "allergy_notes": "; ".join(
            f"{attendee_label(i, a, c['guest_name'])}: {a['allergy']}" for i, a in enumerate(atts) if a["allergy"]
        ),
        "preferred_dishes": c["preferred_dishes"] if coming else "",
        "alcohol_prefs": ", ".join(c["alcohol_prefs"]) if coming else "",
        "non_alcohol_prefs": ", ".join(c["non_alcohol_prefs"]) if coming else "",
        "special_requests": c["special_requests"],
        "edit_count": str(to_int(c.get("edit_count"), 0)),
        "last_edited_by": "guest",
    }
    for k in DRINK_TEXT_FIELDS:
        rec[k] = c[k] if coming else ""
    return rec


def food_display(a: dict) -> str:
    f = _s(a.get("food"))
    if not f:
        return FOOD_UNKNOWN
    if f == FOOD_OTHER and _s(a.get("food_other")):
        return f"Other ({_s(a.get('food_other'))})"
    return f


def record_attendees(r: dict) -> list[dict]:
    try:
        data = json.loads(r.get("attendees_json") or "[]")
        return [dict(blank_attendee(), **a) for a in data if isinstance(a, dict)]
    except (ValueError, TypeError):
        return []


def draft_from_record(r: dict) -> dict:
    d = new_draft(r.get("status"))
    d["rsvp_id"] = _s(r.get("rsvp_id"))
    for k in ("guest_name", "email", "phone", "dietary_restrictions", "preferred_dishes",
              "special_requests", "submitted_at", *DRINK_TEXT_FIELDS):
        d[k] = _s(r.get(k))
    d["party_size"] = max(1, to_int(r.get("party_size"), 1))
    d["attendees"] = record_attendees(r) or [blank_attendee(d["guest_name"])]
    d["alcohol_prefs"] = _dedupe(split_list(r.get("alcohol_prefs")), ALCOHOL_CHOICES)
    d["non_alcohol_prefs"] = _dedupe(split_list(r.get("non_alcohol_prefs")), NON_ALCOHOL_OPTIONS)
    d["edit_count"] = to_int(r.get("edit_count"), 0)
    return resize_attendees(d)


# ---------------------------------------------------------------------
# Deadline & registration gate (enforced server-side at submit time)
# ---------------------------------------------------------------------
def parse_local_datetime(s: str) -> datetime:
    if is_placeholder(s):
        raise ValueError("not set")
    return datetime.strptime(_s(s), DEADLINE_FORMAT).replace(tzinfo=TZ)


def parse_time(s: str) -> time | None:
    if is_placeholder(s):
        return None
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            return datetime.strptime(_s(s).upper(), fmt).time()
        except ValueError:
            continue
    return None


def event_start(event_date: date, start_time: str) -> datetime | None:
    t = parse_time(start_time)
    return datetime.combine(event_date, t, tzinfo=TZ) if t else None


def event_cutoff(event_date: date, start_time: str) -> datetime:
    """Latest allowed deadline: party start, or end of party day if no start time."""
    return event_start(event_date, start_time) or datetime.combine(event_date, time(23, 59), tzinfo=TZ)


def check_deadline(deadline_str: str, event_date: date, start_time: str) -> tuple[datetime | None, str | None]:
    """Structural check (format, before event). Returns (deadline, problem)."""
    if is_placeholder(deadline_str):
        return None, "RSVP deadline has not been set."
    try:
        dl = parse_local_datetime(deadline_str)
    except ValueError:
        return None, f"RSVP deadline \"{deadline_str}\" is not in YYYY-MM-DD HH:MM format."
    if dl > event_cutoff(event_date, start_time):
        return None, "RSVP deadline is after the party starts."
    return dl, None


def validate_deadline_for_save(deadline_str: str, event_date: date, start_time: str, now: datetime) -> list[str]:
    dl, problem = check_deadline(deadline_str, event_date, start_time)
    if problem:
        return [problem]
    if dl <= now:
        return ["RSVP deadline must be in the future."]
    return []


@dataclass(frozen=True)
class Gate:
    state: str                 # "open" | "closed" | "not_configured"
    allow_new: bool
    allow_edit: bool
    deadline: datetime | None
    reason: str


def registration_gate(now: datetime, deadline_str: str, event_date: date, start_time: str,
                      mode: str, allow_edits_after_deadline: bool) -> Gate:
    dl, problem = check_deadline(deadline_str, event_date, start_time)
    mode = (mode or "auto").lower()
    if mode == "closed":
        return Gate("closed", False, bool(allow_edits_after_deadline), dl, "Closed by the hosts.")
    if mode == "open":
        return Gate("open", True, True, dl, "Opened by the hosts.")
    if problem:
        return Gate("not_configured", False, False, None, problem)
    if now < dl:
        return Gate("open", True, True, dl, "Before the RSVP deadline.")
    return Gate("closed", False, bool(allow_edits_after_deadline), dl, "The RSVP deadline has passed.")


def fmt_deadline(dl: datetime) -> str:
    return dl.strftime("%a, %d %b · %I:%M %p IST").replace(" 0", " ")


def fmt_event_date(d: date) -> str:
    return d.strftime("%A, %d %B %Y").replace(" 0", " ")


def fmt_time(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


# ---------------------------------------------------------------------
# Edit tokens — raw token goes to the guest; only an HMAC hash is stored
# ---------------------------------------------------------------------
def generate_edit_token() -> str:
    return secrets.token_urlsafe(24)  # 192 bits, 32 URL-safe characters


def hash_edit_token(token: str, pepper: str) -> str:
    if not pepper:
        raise ValueError("token pepper is not configured")
    return hmac.new(pepper.encode(), _s(token).encode(), hashlib.sha256).hexdigest()


def token_matches(token: str, stored_hash: str, pepper: str) -> bool:
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_edit_token(token, pepper), _s(stored_hash))


def extract_token(text: str) -> str | None:
    """Accept either a raw code or a pasted edit link."""
    s = _s(text)
    m = re.search(r"[?&]edit=([A-Za-z0-9_\-]+)", s)
    if m:
        s = m.group(1)
    return s if TOKEN_RE.match(s) else None


def edit_link(app_url: str, token: str) -> str:
    return f"{app_url.rstrip('/')}/?edit={token}" if app_url else ""


# ---------------------------------------------------------------------
# Aggregation for the host dashboard (computed from authoritative rows)
# ---------------------------------------------------------------------
def status_of(r: dict) -> str:
    return _s(r.get("status"))


def summarize(records: list[dict], invitees: list[str] | None = None) -> dict:
    out = {
        "total_rsvps": len(records),
        "attending_rsvps": 0, "confirmed_people": 0,
        "tentative_rsvps": 0, "tentative_people": 0,
        "declined_rsvps": 0, "unknown_status_rsvps": 0,
        "non_responses": None, "non_responders": [],
    }
    for r in records:
        st = status_of(r)
        size = max(0, to_int(r.get("party_size"), 0))
        if st == ATTENDING:
            out["attending_rsvps"] += 1
            out["confirmed_people"] += max(1, size)
        elif st == TENTATIVE:
            out["tentative_rsvps"] += 1
            out["tentative_people"] += max(1, size)
        elif st == DECLINED:
            out["declined_rsvps"] += 1
        else:
            out["unknown_status_rsvps"] += 1  # never assumed to be a decline
    if invitees is not None:
        replied = set()
        for r in records:
            replied.add(normalize_name(r.get("guest_name")))
            for n in split_list(r.get("additional_guests")):
                replied.add(normalize_name(n))
        pending = [n for n in invitees if normalize_name(n) and normalize_name(n) not in replied]
        out["non_responses"] = len(pending)
        out["non_responders"] = pending
    return out


def people_rows(records: list[dict], statuses=(ATTENDING,)) -> list[dict]:
    """One row per expected person. Missing per-person data → Unknown, so
    the row count always equals the party sizes."""
    rows = []
    for r in records:
        st = status_of(r)
        if st not in statuses:
            continue
        size = max(1, to_int(r.get("party_size"), 1))
        atts = record_attendees(r)
        for i in range(size):
            a = atts[i] if i < len(atts) else blank_attendee()
            food = _s(a.get("food"))
            rows.append({
                "rsvp_guest": _s(r.get("guest_name")),
                "person": attendee_label(i, a, r.get("guest_name")),
                "status": st,
                "food": food if food in FOOD_OPTIONS else FOOD_UNKNOWN,
                "food_other": _s(a.get("food_other")),
                "allergy": _s(a.get("allergy")),
            })
    return rows


def food_counts(records: list[dict], statuses=(ATTENDING,)) -> dict[str, int]:
    counts = {k: 0 for k in FOOD_OPTIONS + [FOOD_UNKNOWN]}
    for p in people_rows(records, statuses):
        counts[p["food"]] += 1
    return counts


def allergy_rows(records: list[dict], statuses=(ATTENDING, TENTATIVE)) -> list[dict]:
    return [p for p in people_rows(records, statuses) if p["allergy"]]


def bar_counts(records: list[dict], statuses=(ATTENDING,)) -> list[dict]:
    rows = {opt: {"drink": opt, "rsvps": 0, "people_in_those_rsvps": 0}
            for opt in ALCOHOL_CHOICES + NON_ALCOHOL_OPTIONS}
    any_na = {"drink": "Any non-alcoholic", "rsvps": 0, "people_in_those_rsvps": 0}
    none_given = {"drink": "No drink preference given", "rsvps": 0, "people_in_those_rsvps": 0}
    for r in records:
        if status_of(r) not in statuses:
            continue
        size = max(1, to_int(r.get("party_size"), 1))
        alc = _dedupe(split_list(r.get("alcohol_prefs")), ALCOHOL_CHOICES)
        na = _dedupe(split_list(r.get("non_alcohol_prefs")), NON_ALCOHOL_OPTIONS)
        for opt in alc + na:
            rows[opt]["rsvps"] += 1
            rows[opt]["people_in_those_rsvps"] += size
        if na:
            any_na["rsvps"] += 1
            any_na["people_in_those_rsvps"] += size
        if not alc and not na:
            none_given["rsvps"] += 1
            none_given["people_in_those_rsvps"] += size
    return list(rows.values()) + [any_na, none_given]


def text_mentions(records: list[dict], fields: dict[str, str], statuses=(ATTENDING, TENTATIVE)) -> list[dict]:
    out = []
    for r in records:
        if status_of(r) not in statuses:
            continue
        for k, label in fields.items():
            v = _s(r.get(k))
            if v:
                out.append({"guest": _s(r.get("guest_name")), "status": status_of(r), "about": label, "note": v})
    return out


def possible_duplicates(records: list[dict]) -> list[list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in records:
        key = normalize_name(r.get("guest_name"))
        if key:
            groups.setdefault(key, []).append(r)
    return [g for g in groups.values() if len(g) > 1]


# ---------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------
def _ics_escape(s: str) -> str:
    return _s(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _event_window(event_date: date, start_time: str, end_time: str):
    start = event_start(event_date, start_time)
    if not start:
        return None, None
    et = parse_time(end_time)
    end = datetime.combine(event_date, et, tzinfo=TZ) if et else start + timedelta(hours=4)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def build_ics(title: str, event_date: date, start_time: str, end_time: str,
              location: str, description: str, uid: str) -> str:
    start, end = _event_window(event_date, start_time, end_time)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if start:
        when = [f"DTSTART:{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
                f"DTEND:{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"]
    else:
        when = [f"DTSTART;VALUE=DATE:{event_date:%Y%m%d}",
                f"DTEND;VALUE=DATE:{event_date + timedelta(days=1):%Y%m%d}"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//party-rsvp//EN", "CALSCALE:GREGORIAN",
             "BEGIN:VEVENT", f"UID:{uid}@party-rsvp", f"DTSTAMP:{stamp}", *when,
             f"SUMMARY:{_ics_escape(title)}"]
    if location:
        lines.append(f"LOCATION:{_ics_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_ics_escape(description)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def google_calendar_url(title: str, event_date: date, start_time: str, end_time: str,
                        location: str, description: str) -> str:
    start, end = _event_window(event_date, start_time, end_time)
    if start:
        dates = f"{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}/{end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
    else:
        dates = f"{event_date:%Y%m%d}/{event_date + timedelta(days=1):%Y%m%d}"
    params = {"action": "TEMPLATE", "text": title, "dates": dates, "details": description,
              "location": location, "ctz": "Asia/Kolkata"}
    return "https://calendar.google.com/calendar/render?" + urlencode({k: v for k, v in params.items() if v}, quote_via=quote)


# ---------------------------------------------------------------------
# CSV export (with spreadsheet formula-injection protection)
# ---------------------------------------------------------------------
def csv_safe(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def to_csv(rows: list[dict], columns: list[str], headers: list[str] | None = None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers or columns)
    for r in rows:
        w.writerow([csv_safe(r.get(c, "")) for c in columns])
    return buf.getvalue()
