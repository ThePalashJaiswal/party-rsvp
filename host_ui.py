"""Private host dashboard. Reached at  <app-url>/?view=host  and protected by
the admin password in Streamlit secrets. Nothing here is rendered for guests."""

from __future__ import annotations

import hmac
import threading
import time
from collections import deque

import pandas as pd
import streamlit as st

import config
import rsvp_logic as L
from google_sheets import StorageError
from ui_common import (admin_password, clear_settings_cache, current_settings, get_store, setup_problems,
                       storage_mode, store_error_detail, token_pepper)

SESSION_HOURS = 12
FAIL_WINDOW_S = 600
FAIL_LIMIT = 8


# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------
@st.cache_resource
def _login_guard():
    """Process-wide throttle shared by every browser session."""
    return {"fails": deque(maxlen=50), "lock": threading.Lock()}


def _locked_out() -> bool:
    g = _login_guard()
    with g["lock"]:
        now = time.time()
        while g["fails"] and now - g["fails"][0] > FAIL_WINDOW_S:
            g["fails"].popleft()
        return len(g["fails"]) >= FAIL_LIMIT


def _record_fail():
    g = _login_guard()
    with g["lock"]:
        g["fails"].append(time.time())


def is_authed() -> bool:
    t = st.session_state.get("host_auth_at")
    return bool(t) and time.time() - t < SESSION_HOURS * 3600


def login_screen():
    st.html('<h1 class="display host-title">Host lounge</h1><p class="lede">Private dashboard for the hosts.</p>')
    pw_conf = admin_password()
    if not pw_conf:
        st.error("No admin password is configured. Add `[admin] password` to your Streamlit secrets, then reload.")
        return
    with st.form("login", border=True):
        pw = st.text_input("Host password", type="password", autocomplete="current-password")
        ok = st.form_submit_button("Enter", type="primary")
    if ok:
        if _locked_out():
            st.error("Too many failed attempts. Please wait 10 minutes and try again.")
            return
        if pw and hmac.compare_digest(pw.encode(), pw_conf.encode()):
            st.session_state.host_auth_at = time.time()
            st.rerun()
        _record_fail()
        time.sleep(1.2)
        st.error("That password isn't right.")


# ---------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def _records(_store, mode: str) -> list[dict]:
    return _store.list_rsvps()


@st.cache_data(ttl=120, show_spinner=False)
def _invitees(_store, mode: str):
    return _store.list_invitees()


def refresh():
    _records.clear()
    _invitees.clear()
    clear_settings_cache()


def drink_display(r: dict) -> str:
    return ", ".join(L.split_list(r.get("alcohol_prefs")) + L.split_list(r.get("non_alcohol_prefs")))


def guests_frame(records: list[dict]) -> pd.DataFrame:
    rows = [{
        "Guest": r.get("guest_name", ""),
        "Status": r.get("status", "") or "(blank)",
        "People": L.to_int(r.get("party_size"), 0),
        "With": r.get("additional_guests", ""),
        "Food": r.get("food_summary", ""),
        "Allergies": r.get("allergy_notes", ""),
        "Drinks": drink_display(r),
        "Requests": r.get("special_requests", ""),
        "Updated": r.get("updated_at", ""),
        "_id": r.get("rsvp_id", ""),
    } for r in records]
    cols = ["Guest", "Status", "People", "With", "Food", "Allergies", "Drinks", "Requests", "Updated", "_id"]
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------
def tab_overview(records, settings, store):
    inv = None
    try:
        inv = _invitees(store, storage_mode())
    except StorageError:
        st.caption("Couldn't read the optional Invitees tab.")
    k = L.summarize(records, inv)
    gate = config.gate_for(settings)

    state_txt = {"open": "Open", "closed": "Closed", "not_configured": "Not open (needs setup)"}[gate.state]
    dl = f" · deadline {L.fmt_deadline(gate.deadline)}" if gate.deadline else ""
    (st.success if gate.state == "open" else st.warning)(f"Registration: **{state_txt}**{dl}. {gate.reason}")

    c = st.columns(3)
    c[0].metric("Confirmed guests (people)", k["confirmed_people"],
                help="Sum of party sizes on 'Attending' RSVPs, including plus-ones.")
    c[1].metric("Attending RSVPs", k["attending_rsvps"], help="Number of RSVP submissions marked Attending.")
    c[2].metric("Total RSVPs received", k["total_rsvps"])
    c = st.columns(3)
    c[0].metric("Tentative RSVPs", k["tentative_rsvps"], help=f"{k['tentative_people']} people if they all come. Not counted as confirmed.")
    c[1].metric("Declined RSVPs", k["declined_rsvps"])
    c[2].metric("Not yet replied", "—" if k["non_responses"] is None else k["non_responses"],
                help="Needs an 'Invitees' tab in the sheet with names in column A. Matching is by name, so treat it as a guide.")
    if k["unknown_status_rsvps"]:
        st.warning(f"{k['unknown_status_rsvps']} RSVP(s) have a blank or unrecognised status. They are not counted as declines.")
    if k["non_responders"]:
        with st.expander(f"Invitees who haven't replied ({len(k['non_responders'])})"):
            st.write(", ".join(k["non_responders"]))

    dups = L.possible_duplicates(records)
    if dups:
        with st.expander(f"Possible duplicate RSVPs ({len(dups)} names)", icon=":material/content_copy:"):
            for g in dups:
                st.write(f"**{g[0].get('guest_name')}**: " +
                         "; ".join(f"{r.get('status')} · {r.get('party_size')} people · updated {r.get('updated_at')}" for r in g))

    if not records:
        st.info("No RSVPs yet. Share the link and they'll appear here.")
        return
    st.subheader("Responses")
    st.bar_chart(pd.DataFrame({"RSVPs": [k["attending_rsvps"], k["tentative_rsvps"], k["declined_rsvps"]]},
                              index=["Attending", "Tentative", "Declined"]), color="#AB0000", horizontal=True)


def tab_guests(records, settings, store):
    df = guests_frame(records)
    c1, c2 = st.columns([2, 1])
    q = c1.text_input("Search name", placeholder="Name or plus-one", key="h_search")
    statuses = c2.multiselect("Status", L.STATUSES + ["(blank)"], default=[], key="h_status",
                              placeholder="All statuses")
    c3, c4 = st.columns(2)
    sort = c3.selectbox("Sort by", ["Last updated (newest)", "Name (A–Z)", "Party size (largest)"], key="h_sort")
    page_size = c4.selectbox("Rows per page", [25, 50, 100], key="h_ps")

    if q:
        ql = q.casefold()
        df = df[df["Guest"].str.casefold().str.contains(ql, regex=False) | df["With"].str.casefold().str.contains(ql, regex=False)]
    if statuses:
        df = df[df["Status"].isin(statuses)]
    if sort.startswith("Name"):
        df = df.sort_values("Guest", key=lambda s: s.str.casefold())
    elif sort.startswith("Party"):
        df = df.sort_values("People", ascending=False)
    else:
        df = df.sort_values("Updated", ascending=False)

    pages = max(1, -(-len(df) // page_size))
    page = st.number_input(f"Page (of {pages})", 1, pages, 1, key="h_page") if pages > 1 else 1
    view = df.iloc[(page - 1) * page_size: page * page_size]
    st.caption(f"{len(df)} matching RSVPs")
    st.dataframe(view.drop(columns=["_id"]), hide_index=True, width="stretch",
                 column_config={"Requests": st.column_config.TextColumn(width="medium")})

    if df.empty:
        return
    st.subheader("Look at or correct one RSVP")
    labels = {row["_id"]: f"{row['Guest']} · {row['Status']} · {row['People']} ppl" for _, row in df.iterrows()}
    rid = st.selectbox("Choose an RSVP", list(labels), format_func=labels.get, key="h_pick")
    rec = next((r for r in records if r.get("rsvp_id") == rid), None)
    if rec:
        correction_form(rec, settings, store)


def correction_form(rec: dict, settings, store):
    d = L.draft_from_record(rec)
    with st.form(f"fix_{rec['rsvp_id']}"):
        st.caption(f"Submitted {rec.get('submitted_at') or '—'} · updated {rec.get('updated_at') or '—'} "
                   f"by {rec.get('last_edited_by') or 'guest'} · edits {rec.get('edit_count') or 0}")
        c1, c2 = st.columns(2)
        name = c1.text_input("Guest name", d["guest_name"], max_chars=L.MAX_NAME)
        status = c2.selectbox("Status", L.STATUSES,
                              index=L.STATUSES.index(d["status"]) if d["status"] in L.STATUSES else 0)
        c1, c2, c3 = st.columns(3)
        email = c1.text_input("Email", d["email"])
        phone = c2.text_input("Mobile", d["phone"])
        size = c3.number_input("People", 1, config.MAX_PARTY_LIMIT, max(1, L.to_int(rec.get("party_size"), 1)),
                               help="Hosts may exceed the guest limit.")
        st.caption("People in this party (one row each). Rows beyond 'People' are ignored.")
        att_df = pd.DataFrame(d["attendees"], columns=["name", "food", "food_other", "allergy"])
        att_df = st.data_editor(att_df, num_rows="dynamic", hide_index=True, width="stretch",
                                column_config={
                                    "name": st.column_config.TextColumn("Name", max_chars=L.MAX_NAME),
                                    "food": st.column_config.SelectboxColumn("Food", options=L.FOOD_OPTIONS),
                                    "food_other": st.column_config.TextColumn("Other diet", max_chars=L.MAX_SHORT),
                                    "allergy": st.column_config.TextColumn("Allergy", max_chars=L.MAX_SHORT),
                                })
        dietary = st.text_input("Dietary restrictions", d["dietary_restrictions"], max_chars=L.MAX_SHORT)
        alc = st.multiselect("Bar", L.ALCOHOL_CHOICES, d["alcohol_prefs"])
        na = st.multiselect("Zero-proof", L.NON_ALCOHOL_OPTIONS, d["non_alcohol_prefs"])
        special = st.text_area("Special requests", d["special_requests"], max_chars=L.MAX_SPECIAL)
        notes = st.text_area("Host notes (never shown to guests)", rec.get("host_notes", ""), max_chars=1000)
        saved = st.form_submit_button("Save correction", type="primary")
    if saved:
        d.update(guest_name=name, status=status, email=email, phone=phone, party_size=int(size),
                 dietary_restrictions=dietary, alcohol_prefs=alc, non_alcohol_prefs=na, special_requests=special,
                 attendees=[{k: ("" if pd.isna(v) else str(v)) for k, v in row.items()}
                            for row in att_df.to_dict("records")])
        L.resize_attendees(d)
        errs = (L.validate_guest_info(d, config.MAX_PARTY_LIMIT) + L.validate_drinks(d) + L.validate_requests(d))
        if errs:
            st.error("\n".join(f"- {x}" for x in errs))
            return
        record = L.record_from_draft(d)
        record.update(updated_at=L.iso(L.now_local()), last_edited_by="host", host_notes=notes)
        record.pop("edit_count", None)
        try:
            store.upsert(record)
        except StorageError as exc:
            st.error(str(exc))
            return
        refresh()
        st.toast("Saved to Google Sheets.", icon=":material/check:")
        st.rerun()

    with st.expander("Guest lost their edit link?"):
        st.caption("Issue a new private link. The old one stops working immediately. It's shown once, so copy it now.")
        if st.button("Issue new edit link", key=f"reissue_{rec['rsvp_id']}"):
            token = L.generate_edit_token()
            try:
                store.upsert({"rsvp_id": rec["rsvp_id"], "edit_token_hash": L.hash_edit_token(token, token_pepper()),
                              "updated_at": L.iso(L.now_local()), "last_edited_by": "host"})
            except StorageError as exc:
                st.error(str(exc))
                return
            refresh()
            st.code(L.edit_link(settings.shown("app_url"), token) or token, language=None)


def tab_food(records):
    counts = L.food_counts(records)
    tent = L.food_counts(records, statuses=(L.TENTATIVE,))
    st.caption("Counted per person for confirmed (Attending) guests. Tentative guests are shown separately.")
    c = st.columns(3)
    for i, opt in enumerate([L.FOOD_VEG, L.FOOD_NONVEG, L.FOOD_VEGAN]):
        c[i].metric(opt, counts[opt], help=f"+{tent[opt]} tentative")
    c = st.columns(3)
    c[0].metric("Other diet", counts[L.FOOD_OTHER], help=f"+{tent[L.FOOD_OTHER]} tentative")
    c[1].metric("Not sure yet", counts[L.FOOD_UNSURE], help=f"+{tent[L.FOOD_UNSURE]} tentative")
    c[2].metric("Unknown (not given)", counts[L.FOOD_UNKNOWN], help=f"+{tent[L.FOOD_UNKNOWN]} tentative")
    if sum(counts.values()):
        st.bar_chart(pd.DataFrame({"Confirmed": counts, "Tentative": tent}), stack=True,
                     color=["#AB0000", "#8C8C8C"], horizontal=True)

    allergies = L.allergy_rows(records)
    st.subheader(f"Allergies needing attention ({len(allergies)})")
    if allergies:
        st.dataframe(pd.DataFrame(allergies)[["person", "rsvp_guest", "status", "allergy", "food"]]
                     .rename(columns={"person": "Person", "rsvp_guest": "RSVP by", "status": "Status",
                                      "allergy": "Allergy", "food": "Food"}), hide_index=True, width="stretch")
    else:
        st.caption("No allergies reported.")
    others = [p for p in L.people_rows(records, (L.ATTENDING, L.TENTATIVE)) if p["food_other"]]
    notes = L.text_mentions(records, {"dietary_restrictions": "Dietary restriction", "preferred_dishes": "Dish wish"})
    if others or notes:
        st.subheader("Dietary notes & dish wishes")
        rows = [{"Guest": p["person"], "Status": p["status"], "About": "Other diet", "Note": p["food_other"]} for p in others]
        rows += [{"Guest": n["guest"], "Status": n["status"], "About": n["about"], "Note": n["note"]} for n in notes]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def tab_bar(records):
    st.caption("Preference counts from confirmed RSVPs, not purchase quantities. Drinks are chosen per RSVP, "
               "so 'people in those RSVPs' is an upper bound on who might want each drink.")
    df = pd.DataFrame(L.bar_counts(records)).rename(
        columns={"drink": "Drink", "rsvps": "RSVPs", "people_in_those_rsvps": "People in those RSVPs"})
    st.dataframe(df, hide_index=True, width="stretch")
    chart = df[~df["Drink"].isin(["Any non-alcoholic", "No drink preference given"])].set_index("Drink")[["RSVPs"]]
    if chart["RSVPs"].sum():
        st.bar_chart(chart, color="#AB0000", horizontal=True)
    notes = L.text_mentions(records, L.DRINK_TEXT_FIELDS, statuses=(L.ATTENDING,))
    st.subheader("Brands, cocktails, mixers & requests")
    if notes:
        st.dataframe(pd.DataFrame(notes).rename(columns={"guest": "Guest", "status": "Status", "about": "About", "note": "Request"}),
                     hide_index=True, width="stretch")
    else:
        st.caption("No specific drink requests yet.")


def tab_exports(records):
    st.caption("Exports are generated from the live sheet. Cells that start with = + - @ are prefixed with ' "
               "so spreadsheet apps can't run them as formulas.")
    stamp = L.now_local().strftime("%Y%m%d-%H%M")
    full_cols = [c for c in L.RSVP_HEADERS if c not in L.PRIVATE_COLUMNS]
    st.download_button("All RSVP records (CSV)", L.to_csv(records, full_cols), f"rsvps-all-{stamp}.csv",
                       "text/csv", icon=":material/download:", help="Includes contact details. Handle with care.")

    people = L.people_rows(records, (L.ATTENDING, L.TENTATIVE))
    st.download_button("Food planning (CSV)",
                       L.to_csv(people, ["person", "rsvp_guest", "status", "food", "food_other", "allergy"],
                                ["Person", "RSVP by", "Status", "Food", "Other diet", "Allergy"]),
                       f"food-planning-{stamp}.csv", "text/csv", icon=":material/restaurant:",
                       help="Per person. No contact details or drink preferences.")

    bar_rows = [{"guest": r.get("guest_name"), "people": r.get("party_size"), "alcohol": r.get("alcohol_prefs"),
                 "non_alcohol": r.get("non_alcohol_prefs"), **{k: r.get(k, "") for k in L.DRINK_TEXT_FIELDS}}
                for r in records if r.get("status") == L.ATTENDING]
    st.download_button("Bar planning (CSV)",
                       L.to_csv(bar_rows, ["guest", "people", "alcohol", "non_alcohol", *L.DRINK_TEXT_FIELDS],
                                ["Guest", "People", "Bar", "Zero-proof", *L.DRINK_TEXT_FIELDS.values()]),
                       f"bar-planning-{stamp}.csv", "text/csv", icon=":material/local_bar:",
                       help="Confirmed RSVPs only. No contact details.")

    st.subheader("Guest list")
    c1, c2 = st.columns([2, 1])
    sel = c1.multiselect("Include statuses", L.STATUSES, default=[L.ATTENDING], key="x_status")
    contact = c2.checkbox("Include contact details", value=False, key="x_contact")
    rows = [r for r in records if r.get("status") in sel]
    cols = ["guest_name", "status", "party_size", "additional_guests"] + (["email", "phone"] if contact else [])
    st.download_button(f"Guest list: {len(rows)} RSVPs (CSV)", L.to_csv(rows, cols), f"guest-list-{stamp}.csv",
                       "text/csv", icon=":material/group:")


def tab_settings(settings, store):
    st.caption("Saved to the Config tab of your Google Sheet, where they override config.py. "
               "Secrets are never stored here.")
    gate = config.gate_for(settings)
    raw, _ = config.merge_raw(store.read_config() if store else {})
    with st.form("settings"):
        c1, c2 = st.columns(2)
        title = c1.text_input("Event title", "" if L.is_placeholder(raw["event_title"]) else raw["event_title"])
        hosts = c2.text_input("Host names", "" if L.is_placeholder(raw["host_names"]) else raw["host_names"])
        venue = st.text_input("Venue", "" if L.is_placeholder(raw["venue"]) else raw["venue"])
        map_url = st.text_input("Map link (https://, optional)", raw["venue_map_url"])
        c1, c2, c3 = st.columns(3)
        start = c1.text_input("Start time (HH:MM)", "" if L.is_placeholder(raw["start_time"]) else raw["start_time"])
        end = c2.text_input("End time (HH:MM, optional)", raw["end_time"])
        dress = c3.text_input("Dress code", "" if L.is_placeholder(raw["dress_code"]) else raw["dress_code"])
        st.markdown(f"**Event date:** {L.fmt_event_date(settings.event_date)} (fixed in `config.py`)")
        c1, c2 = st.columns(2)
        deadline = c1.text_input("RSVP deadline (YYYY-MM-DD HH:MM, IST)",
                                 "" if L.is_placeholder(raw["rsvp_deadline"]) else raw["rsvp_deadline"])
        mps = c2.number_input("Max people per RSVP", 1, config.MAX_PARTY_LIMIT, settings.max_party_size)
        mode = st.radio("Registration", list(L.REGISTRATION_MODES), index=L.REGISTRATION_MODES.index(settings.registration_mode),
                        horizontal=True, format_func={"auto": "Follow deadline", "open": "Force open",
                                                      "closed": "Force closed"}.get)
        edits = st.checkbox("Let guests edit their RSVP after registration closes", settings.allow_edits_after_deadline)
        contact = st.text_input("Host contact shown when closed", raw["host_contact"])
        headline = st.text_input("Welcome headline", raw["headline"])
        hero = st.text_input("Hero image URL (https://, optional)", raw["hero_image_url"],
                             help="Or commit an image to assets/hero.jpg in GitHub.")
        app_url = st.text_input("Public app URL (for guests' edit links)", raw["app_url"],
                                placeholder="https://your-app.streamlit.app")
        saved = st.form_submit_button("Save settings", type="primary")
    st.caption(f"Current state: **{gate.state.replace('_', ' ')}**. {gate.reason}")
    if not saved:
        return
    errs = []
    if start and not L.parse_time(start):
        errs.append("Start time must look like 19:30.")
    if end and not L.parse_time(end):
        errs.append("End time must look like 23:00.")
    for label, url in (("Map link", map_url), ("Hero image URL", hero), ("App URL", app_url)):
        if url and not url.startswith("https://"):
            errs.append(f"{label} must start with https://")
    if deadline:
        errs += L.validate_deadline_for_save(deadline, settings.event_date, start, L.now_local())
    elif mode == "auto":
        st.warning("No deadline set: guests will see 'RSVPs open soon' until you add one or force registration open.")
    if errs:
        st.error("\n".join(f"- {x}" for x in errs))
        return
    updates = {
        "event_title": title, "host_names": hosts, "venue": venue, "venue_map_url": map_url,
        "start_time": start, "end_time": end, "dress_code": dress, "rsvp_deadline": deadline,
        "max_party_size": str(int(mps)), "registration_mode": mode,
        "allow_edits_after_deadline": "true" if edits else "false", "host_contact": contact,
        "headline": headline, "hero_image_url": hero, "app_url": app_url.rstrip("/"),
    }
    try:
        store.write_config(updates, config.SETTING_NOTES)
    except StorageError as exc:
        st.error(str(exc))
        return
    refresh()
    st.toast("Settings saved.", icon=":material/check:")
    st.rerun()


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
def render_host():
    if not is_authed():
        login_screen()
        return
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.html('<h1 class="display host-title">Host lounge</h1>')
    if c2.button("Refresh", icon=":material/refresh:", width="stretch"):
        refresh()
        st.rerun()
    if c3.button("Log out", width="stretch"):
        st.session_state.pop("host_auth_at", None)
        st.rerun()

    probs = setup_problems()
    if probs:
        st.error("Setup incomplete:\n" + "\n".join(f"- {p}" for p in probs))
    if storage_mode() == "memory":
        st.warning("Demo mode: data lives in memory and disappears on restart. Not for real guests.")
    store = get_store()
    if store is None:
        st.error(store_error_detail() or "Google Sheets is not configured yet. See README → Deploy.")
        return
    settings = current_settings(store)
    try:
        records = _records(store, storage_mode())
    except StorageError as exc:
        st.error(str(exc))
        return
    tabs = st.tabs(["Overview", "Guests", "Food", "Bar", "Exports", "Settings"])
    with tabs[0]:
        tab_overview(records, settings, store)
    with tabs[1]:
        tab_guests(records, settings, store)
    with tabs[2]:
        tab_food(records)
    with tabs[3]:
        tab_bar(records)
    with tabs[4]:
        tab_exports(records)
    with tabs[5]:
        tab_settings(settings, store)
