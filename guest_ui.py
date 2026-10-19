"""Guest-facing RSVP journey: welcome → details → food → drinks → requests → review → done."""

from __future__ import annotations

from urllib.parse import quote

import streamlit as st

import config
import rsvp_logic as L
from google_sheets import StorageError
from ui_common import (ICON_CAL, ICON_CLOCK, ICON_HAT, ICON_PIN, chip, confetti_html, current_settings, toast_html,
                       e, get_store, hero_html, progress_html, storage_mode, token_pepper)

STEP_LABELS = {"info": "You", "food": "Food", "drinks": "Drinks", "requests": "Extras", "review": "Review"}
STATUS_LABELS = {L.ATTENDING: "I'll be there", L.TENTATIVE: "Not sure yet", L.DECLINED: "Can't make it"}

FOOD_ICONS = {
    L.FOOD_VEG: ":material/eco:", L.FOOD_NONVEG: ":material/kebab_dining:", L.FOOD_VEGAN: ":material/spa:",
    L.FOOD_OTHER: ":material/restaurant:", L.FOOD_UNSURE: ":material/help:",
}
DRINK_ICONS = {
    "Beer": ":material/sports_bar:", "Vodka": ":material/liquor:", "Whiskey": ":material/liquor:",
    "Rum": ":material/liquor:", "Gin": ":material/liquor:", "Wine": ":material/wine_bar:",
    "Cocktails": ":material/local_bar:", "Other alcoholic": ":material/add:", L.NO_ALCOHOL: ":material/no_drinks:",
    "Mocktails": ":material/local_bar:", "Soft drinks": ":material/local_drink:", "Juices": ":material/blender:",
    "Sparkling water": ":material/water_drop:", "Other non-alcoholic": ":material/add:",
}
DRINK_LABELS = {"Other alcoholic": "Something else", "Other non-alcoholic": "Something else "}


def steps_for(status: str) -> list[str]:
    return ["info", "requests", "review"] if status == L.DECLINED else ["info", "food", "drinks", "requests", "review"]


# ---------------------------------------------------------------------
# Session state (transient UI only — the sheet is the source of truth)
# ---------------------------------------------------------------------
def _s():
    return st.session_state


def init_state():
    s = _s()
    s.setdefault("step", "welcome")
    s.setdefault("draft", None)
    s.setdefault("mode", "new")          # "new" | "edit"
    s.setdefault("errors", [])
    s.setdefault("edit_token", None)     # raw token: only held in this guest's session
    s.setdefault("pending_token", None)  # token minted for a NEW rsvp, reused on retry
    s.setdefault("result", None)
    s.setdefault("seeded_for", None)
    s.setdefault("notice", "")


def reset_journey():
    for k in ("draft", "edit_token", "pending_token", "result", "seeded_for"):
        _s()[k] = None
    _s().step, _s().mode, _s().errors, _s().notice = "welcome", "new", [], ""


def start(status: str):
    s = _s()
    if s.draft is None or s.mode == "edit" and s.step == "done":
        s.draft = L.new_draft(status)
        s.mode = "new"
    else:
        s.draft["status"] = status
    s.step, s.errors, s.seeded_for = "info", [], None


def goto(step: str):
    _s().step, _s().errors, _s().seeded_for = step, [], None


def nav(direction: int, collect=None, validate=None):
    def cb():
        s = _s()
        if collect:
            collect()
        if direction > 0 and validate:
            errs = validate()
            if errs:
                s.errors = errs
                return
        steps = steps_for(s.draft["status"])
        i = steps.index(s.step) if s.step in steps else 0
        j = i + direction
        s.step = "welcome" if j < 0 else steps[min(j, len(steps) - 1)]
        s.errors, s.seeded_for = [], None
    return cb


def seed(values: dict):
    """Pre-fill widgets from the draft once per step visit."""
    key = (_s().step, _s().draft["rsvp_id"])
    if _s().seeded_for == key:
        return
    for k, v in values.items():
        _s()[k] = v
    _s().seeded_for = key


def show_errors():
    if _s().errors:
        st.error("\n".join(f"- {m}" for m in _s().errors), icon=":material/error:")


# ---------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------
def event_chips(settings: config.Settings) -> str:
    chips = [chip(ICON_CAL, L.fmt_event_date(settings.event_date))]
    t = L.parse_time(settings.start_time)
    if t:
        end = L.parse_time(settings.end_time)
        chips.append(chip(ICON_CLOCK, L.fmt_time(t) + (f" – {L.fmt_time(end)}" if end else " onwards")))
    if settings.shown("venue"):
        v = e(settings.shown("venue"))
        if settings.shown("venue_map_url").startswith("https://"):
            v = f'<a href="{e(settings.shown("venue_map_url"))}" target="_blank" rel="noopener">{v}</a>'
        chips.append(f'<span class="chip">{ICON_PIN}<span>{v}</span></span>')
    return f'<div class="chips">{"".join(chips)}</div>'


def dress_code_html(settings: config.Settings, reminder: bool = False) -> str:
    dc = settings.shown("dress_code")
    if not dc:
        return ""
    label = "Don't forget your costume" if reminder else "Dress code"
    return (f'<div class="dresscode">{ICON_HAT}<div><p class="dc-label">{label}</p>'
            f'<p class="dc-text">{e(dc)}</p></div></div>')


def screen_welcome(settings, gate, store):
    title = settings.shown("event_title") or "You're invited"
    hosts = settings.shown("host_names")
    st.html(f"""
      <section class="welcome fade-up">
        {hero_html(settings)}
        <div class="squiggle" aria-hidden="true"></div>
        <p class="eyebrow">You're invited to a Halloween sundowner</p>
        <h1 class="display">{e(title)}</h1>
        {f'<p class="hosts">Hosted by {e(hosts)}</p>' if hosts else ''}
        <p class="headline">{e(settings.shown("headline"))}</p>
        {event_chips(settings)}
        {dress_code_html(settings)}
        {f'<p class="deadline">Kindly reply by <strong>{e(L.fmt_deadline(gate.deadline))}</strong></p>'
         if gate.state == "open" and gate.deadline else ''}
      </section>""")

    if store is None:
        st.html('<div class="notice">RSVPs aren\'t open just yet. The hosts are putting the finishing touches on things, so please check back soon.</div>')
        return
    if gate.state == "not_configured":
        st.html('<div class="notice">RSVPs open soon. The hosts are putting the finishing touches on the invite, so please check back shortly.</div>')
        return
    if gate.state == "closed":
        contact = settings.shown("host_contact")
        st.html(f"""<div class="notice closed"><strong>RSVPs have closed.</strong>
            Thank you so much for your interest. If you need help with your reply, please get in touch with the hosts directly{': ' + e(contact) if contact else '.'}</div>""")
        if gate.allow_edit:
            edit_box(settings, store, expanded=False)
        return

    c1, c2 = st.columns(2)
    c1.button("I'm In!", type="primary", width="stretch", on_click=start, args=(L.ATTENDING,), key="cta_yes")
    c2.button("Sorry, Can't Make It", width="stretch", on_click=start, args=(L.DECLINED,), key="cta_no")
    st.button("Not sure yet? Let us know", type="tertiary", on_click=start, args=(L.TENTATIVE,), key="cta_maybe")
    edit_box(settings, store, expanded=False)


def edit_box(settings, store, expanded: bool):
    with st.expander("Already replied? Change your RSVP", expanded=expanded, icon=":material/edit:"):
        st.caption("Paste the private link or code you saved when you replied.")
        code = st.text_input("Your private edit link or code", key="w_edit_code", max_chars=300)
        if st.button("Open my RSVP", key="btn_open_edit"):
            open_edit(code, store, settings)


def open_edit(code: str, store, settings):
    token = L.extract_token(code)
    if not token:
        st.error("That code doesn't look right. Please paste the full link or code exactly as you saved it.")
        return
    gate = config.gate_for(settings)
    if not gate.allow_edit:
        st.error("Changes are closed for this party. Please contact the hosts if you need to update your reply.")
        return
    try:
        rec = store.get_by_token(token, token_pepper())
    except StorageError as exc:
        st.error(str(exc))
        return
    if not rec:
        st.error("We couldn't find an RSVP for that code. Please check it, or contact the hosts.")
        return
    s = _s()
    s.draft = L.draft_from_record(rec)
    s.mode, s.edit_token, s.pending_token = "edit", token, None
    s.notice = f"Welcome back, {rec.get('guest_name') or 'friend'}. Make any changes and save them on the review screen."
    goto("info")
    st.rerun()


def screen_info(settings):
    d = _s().draft
    mps = settings.max_party_size
    vals = {"w_name": d["guest_name"], "w_status": d["status"], "w_email": d["email"], "w_phone": d["phone"],
            "w_party": min(max(1, L.to_int(d["party_size"], 1)), mps)}
    for i in range(1, mps):
        vals[f"w_extra_{i}"] = d["attendees"][i]["name"] if i < len(d["attendees"]) else ""
    seed(vals)

    st.html('<h2 class="step-title">First, a little about you</h2>')
    if L.to_int(d["party_size"], 1) > mps:
        st.warning(f"The hosts have set a limit of {mps} people per RSVP, so we've adjusted your party size.")
    st.text_input("Full name", key="w_name", max_chars=L.MAX_NAME, placeholder="As your friends know you")
    status = st.segmented_control("Can you make it?", L.STATUSES, key="w_status", format_func=STATUS_LABELS.get,
                                  required=True, width="stretch")
    if status in (L.ATTENDING, L.TENTATIVE):
        party = st.number_input("How many of you, including yourself?", min_value=1, max_value=mps, step=1,
                                key="w_party", help=f"Up to {mps} people per RSVP.")
        if status == L.TENTATIVE:
            st.caption("We'll pencil you in, but won't count you as confirmed until you let us know.")
        if party > 1:
            st.html('<p class="sub">Who\'s coming with you? <span>(optional)</span></p>')
            for i in range(1, int(party)):
                st.text_input(f"Guest {i + 1} name", key=f"w_extra_{i}", max_chars=L.MAX_NAME,
                              placeholder=f"Guest {i + 1}")
    with st.container(key="card_contact"):
        st.html('<p class="sub">How can we reach you? <span>(optional)</span></p>')
        st.text_input("Email", key="w_email", max_chars=L.MAX_EMAIL, placeholder="you@example.com")
        st.text_input("Mobile number", key="w_phone", max_chars=L.MAX_PHONE, placeholder="+91 98xxx xxxxx")

    show_errors()
    nav_buttons(collect_info, lambda: L.validate_guest_info(_s().draft, mps), back=_s().mode == "new")


def collect_info():
    s, d = _s(), _s().draft
    d["guest_name"] = s.get("w_name", d["guest_name"])
    d["status"] = s.get("w_status") or d["status"]
    d["email"] = s.get("w_email", d["email"])
    d["phone"] = s.get("w_phone", d["phone"])
    if d["status"] != L.DECLINED:
        d["party_size"] = s.get("w_party", d["party_size"])
        L.resize_attendees(d)
        for i in range(1, len(d["attendees"])):
            d["attendees"][i]["name"] = s.get(f"w_extra_{i}", d["attendees"][i]["name"])
    L.resize_attendees(d)


def screen_food(settings):
    d = _s().draft
    vals = {"w_dietary": d["dietary_restrictions"], "w_dishes": d["preferred_dishes"]}
    for i, a in enumerate(d["attendees"]):
        vals.update({f"w_food_{i}": a["food"] or None, f"w_food_other_{i}": a["food_other"],
                     f"w_has_allergy_{i}": bool(a["allergy"]), f"w_allergy_{i}": a["allergy"]})
    seed(vals)

    st.html('<h2 class="step-title">What would you like to eat?</h2>'
            '<p class="lede">Pick a preference for each person. We keep allergies separate, so please flag them below.</p>')
    for i, a in enumerate(d["attendees"]):
        name = L.attendee_label(i, a, d["guest_name"])
        with st.container(key=f"card_person_{i}"):
            st.html(f'<p class="person">{e(name)}{" <span>(you)</span>" if i == 0 else ""}</p>')
            food = st.pills(f"Food preference for {name}", L.FOOD_OPTIONS, key=f"w_food_{i}",
                            format_func=lambda o: f"{FOOD_ICONS[o]} {o}", label_visibility="collapsed")
            if food == L.FOOD_OTHER:
                st.text_input("Tell us your dietary preference", key=f"w_food_other_{i}", max_chars=L.MAX_SHORT,
                              placeholder="e.g. Jain, pescatarian, eggetarian")
            if st.toggle("Has a food allergy", key=f"w_has_allergy_{i}"):
                st.text_input("Allergy details", key=f"w_allergy_{i}", max_chars=L.MAX_SHORT,
                              placeholder="e.g. peanuts (severe), shellfish")
    has_extra = bool(d["dietary_restrictions"] or d["preferred_dishes"])
    with st.expander("Dietary restrictions or dish wishes (optional)", expanded=has_extra):
        st.text_input("Dietary restrictions", key="w_dietary", max_chars=L.MAX_SHORT,
                      placeholder="e.g. no onion-garlic, gluten-free, halal")
        st.text_input("Dishes you'd love to see", key="w_dishes", max_chars=L.MAX_SHORT,
                      placeholder="e.g. grilled paneer, prawn skewers")
    show_errors()
    nav_buttons(collect_food, lambda: L.validate_food(_s().draft))


def collect_food():
    s, d = _s(), _s().draft
    for i, a in enumerate(d["attendees"]):
        a["food"] = s.get(f"w_food_{i}") or ""
        a["food_other"] = s.get(f"w_food_other_{i}", "") if a["food"] == L.FOOD_OTHER else ""
        a["allergy"] = s.get(f"w_allergy_{i}", "") if s.get(f"w_has_allergy_{i}") else ""
    d["dietary_restrictions"] = s.get("w_dietary", d["dietary_restrictions"])
    d["preferred_dishes"] = s.get("w_dishes", d["preferred_dishes"])


def _alcohol_changed():
    s = _s()
    cur = list(s.get("w_alcohol") or [])
    prev = list(s.get("_alc_prev") or [])
    if L.NO_ALCOHOL in cur and L.NO_ALCOHOL not in prev:
        cur = [L.NO_ALCOHOL]
    elif L.NO_ALCOHOL in cur and len(cur) > 1:
        cur = [c for c in cur if c != L.NO_ALCOHOL]
    s.w_alcohol = cur
    s._alc_prev = cur


DRINK_FIELD_KEYS = {"beer_pref": "w_beer", "spirits_pref": "w_spirits", "wine_pref": "w_wine",
                    "cocktail_pref": "w_cocktail", "mixer_pref": "w_mixer", "brand_pref": "w_brand",
                    "other_drinks": "w_other_drinks"}


def _drink_conditions(alc: list[str], na: list[str]) -> dict[str, bool]:
    spirits = any(x in L.SPIRITS for x in alc)
    return {
        "beer_pref": "Beer" in alc,
        "spirits_pref": spirits,
        "wine_pref": "Wine" in alc,
        "cocktail_pref": "Cocktails" in alc or "Mocktails" in na,
        "mixer_pref": spirits or "Cocktails" in alc,
        "brand_pref": bool([x for x in alc if x != L.NO_ALCOHOL] or na),
        "other_drinks": True,
    }


def screen_drinks(settings):
    d = _s().draft
    vals = {"w_alcohol": list(d["alcohol_prefs"]), "w_nonalc": list(d["non_alcohol_prefs"]),
            "_alc_prev": list(d["alcohol_prefs"])}
    vals.update({w: d[f] for f, w in DRINK_FIELD_KEYS.items()})
    seed(vals)

    st.html('<h2 class="step-title">Pick your potion</h2>'
            '<p class="lede">What\'s your sundowner of choice? Pick as many as you like, or none at all. '
            'This just helps us stock the cauldron.</p>')
    with st.container(key="card_alc"):
        alc = st.pills("From the bar", L.ALCOHOL_CHOICES, selection_mode="multi", key="w_alcohol",
                       on_change=_alcohol_changed,
                       format_func=lambda o: f"{DRINK_ICONS[o]} {DRINK_LABELS.get(o, o)}")
    with st.container(key="card_na"):
        na = st.pills("Zero-proof", L.NON_ALCOHOL_OPTIONS, selection_mode="multi", key="w_nonalc",
                      format_func=lambda o: f"{DRINK_ICONS[o]} {DRINK_LABELS.get(o, o).strip()}")
    cond = _drink_conditions(alc or [], na or [])
    prompts = {
        "beer_pref": ("Beer style or brand", "e.g. wheat beer, Kingfisher Ultra"),
        "spirits_pref": ("Your whiskey, vodka, rum or gin", "e.g. single malt, Bombay Sapphire"),
        "wine_pref": ("Wine you enjoy", "e.g. a crisp Sauvignon Blanc"),
        "cocktail_pref": ("Favourite cocktails or mocktails", "e.g. mojito, margarita, virgin piña colada"),
        "mixer_pref": ("Preferred mixers", "e.g. tonic, ginger ale, cola"),
        "brand_pref": ("Any brands you'd especially enjoy?", "Optional"),
        "other_drinks": ("Anything unusual you'd love us to stock?", "Your favourite, however niche"),
    }
    if any(v for k, v in cond.items() if k != "other_drinks"):
        st.html('<p class="sub">A few details <span>(all optional)</span></p>')
    for f, w in DRINK_FIELD_KEYS.items():
        if cond[f]:
            label, ph = prompts[f]
            st.text_input(label, key=w, max_chars=L.MAX_SHORT, placeholder=ph)
    show_errors()
    nav_buttons(collect_drinks, lambda: L.validate_drinks(_s().draft))


def collect_drinks():
    s, d = _s(), _s().draft
    d["alcohol_prefs"] = list(s.get("w_alcohol") or [])
    d["non_alcohol_prefs"] = list(s.get("w_nonalc") or [])
    cond = _drink_conditions(d["alcohol_prefs"], d["non_alcohol_prefs"])
    for f, w in DRINK_FIELD_KEYS.items():
        d[f] = s.get(w, d[f]) if cond[f] else ""


def screen_requests(settings):
    d = _s().draft
    seed({"w_special": d["special_requests"]})
    declined = d["status"] == L.DECLINED
    if declined:
        st.html('<h2 class="step-title">We\'ll miss you</h2>'
                '<p class="lede">Thank you for letting us know. Want to leave the hosts a note?</p>')
        label = "A note for the hosts (optional)"
    else:
        st.html('<h2 class="step-title">Anything else we can do to make your evening extra special?</h2>'
                '<p class="lede">Accessibility needs, arrival timing, food or drink requests, anything at all.</p>')
        label = "Special requests (optional)"
    st.text_area(label, key="w_special", max_chars=L.MAX_SPECIAL, height=150,
                 placeholder="e.g. we'll arrive around 8, one of us uses a wheelchair, a high chair for our toddler")
    show_errors()
    nav_buttons(collect_requests, lambda: L.validate_requests(_s().draft))


def collect_requests():
    _s().draft["special_requests"] = _s().get("w_special", _s().draft["special_requests"])


def nav_buttons(collect, validate, back: bool = True):
    c1, c2 = st.columns([1, 2])
    if back:
        c1.button("Back", on_click=nav(-1, collect), width="stretch", key=f"back_{_s().step}")
    c2.button("Continue", type="primary", on_click=nav(+1, collect, validate), width="stretch",
              key=f"next_{_s().step}")


def summary_html(d: dict) -> str:
    c = L.clean_draft(L.resize_attendees(dict(d)))
    coming = c["status"] != L.DECLINED
    rows = [("Name", c["guest_name"]), ("Reply", STATUS_LABELS.get(c["status"], c["status"]))]
    if coming:
        rows.append(("People", str(c["party_size"])))
        extras = [L.attendee_label(i, a) for i, a in enumerate(c["attendees"]) if i > 0]
        if extras:
            rows.append(("With", ", ".join(extras)))
    if c["email"]:
        rows.append(("Email", c["email"]))
    if c["phone"]:
        rows.append(("Mobile", c["phone"]))
    out = ['<div class="summary"><h3>Your details</h3><dl>']
    out += [f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows]
    out.append("</dl>")
    if coming:
        out.append("<h3>Food</h3><ul>")
        for i, a in enumerate(c["attendees"]):
            line = f"<strong>{e(L.attendee_label(i, a, c['guest_name']))}</strong> · {e(L.food_display(a))}"
            if a["allergy"]:
                line += f' <span class="allergy">Allergy: {e(a["allergy"])}</span>'
            out.append(f"<li>{line}</li>")
        out.append("</ul>")
        for k, lbl in (("dietary_restrictions", "Dietary restrictions"), ("preferred_dishes", "Dish wishes")):
            if c[k]:
                out.append(f'<p class="kv"><span>{lbl}</span> {e(c[k])}</p>')
        out.append("<h3>Drinks</h3>")
        drinks = c["alcohol_prefs"] + c["non_alcohol_prefs"]
        out.append(f'<p>{e(", ".join(DRINK_LABELS.get(x, x).strip() for x in drinks)) if drinks else "No preference given"}</p>')
        for k, lbl in L.DRINK_TEXT_FIELDS.items():
            if c[k]:
                out.append(f'<p class="kv"><span>{lbl}</span> {e(c[k])}</p>')
    if c["special_requests"]:
        out.append(f'<h3>{"Note" if not coming else "Special requests"}</h3><p class="pre">{e(c["special_requests"])}</p>')
    out.append("</div>")
    return "".join(out)


def screen_review(settings, store):
    d = _s().draft
    st.html('<h2 class="step-title">Does this look right?</h2>')
    st.html(summary_html(d))
    steps = [x for x in steps_for(d["status"]) if x != "review"]
    with st.container(horizontal=True, key="review_jumps"):
        for stp in steps:
            st.button(f"Edit {STEP_LABELS[stp].lower()}", key=f"jump_{stp}", on_click=goto, args=(stp,),
                      type="tertiary", icon=":material/edit:")
    show_errors()
    label = "Save my changes" if _s().mode == "edit" else "Send my RSVP"
    c1, c2 = st.columns([1, 2])
    c1.button("Back", on_click=nav(-1), width="stretch", key="back_review")
    if c2.button(label, type="primary", width="stretch", key="submit", icon=":material/send:"):
        with st.spinner("Saving your reply…"):
            ok = submit(store)
        if ok:
            st.rerun()
        else:
            show_errors()


def submit(store) -> bool:
    """Server-side gate + validation + idempotent write. Returns True only
    after Google Sheets confirms the write."""
    s = _s()
    d = L.resize_attendees(s.draft)
    try:
        settings = current_settings(store, fresh=True)  # never trust a cached gate here
    except StorageError as exc:
        s.errors = [str(exc)]
        return False
    gate = config.gate_for(settings)
    if s.mode == "new" and not gate.allow_new:
        s.errors = ["Sorry, RSVPs have closed, so we couldn't record this reply. Please contact the hosts."]
        return False
    if s.mode == "edit" and not gate.allow_edit:
        s.errors = ["Sorry, changes are closed for this party. Please contact the hosts."]
        return False
    errs = L.validate_all(d, settings.max_party_size)
    if errs:
        s.errors = errs
        return False

    pepper = token_pepper()
    now = L.iso(L.now_local())
    record = L.record_from_draft(d)
    record["updated_at"] = now
    try:
        if s.mode == "edit":
            current = store.get_by_token(s.edit_token, pepper)  # re-authorise on every save
            if not current or current.get("rsvp_id") != d["rsvp_id"]:
                s.errors = ["Your edit link is no longer valid. Please contact the hosts."]
                return False
            record["edit_count"] = str(L.to_int(current.get("edit_count"), 0) + 1)
            token = s.edit_token
        else:
            if not s.pending_token:
                s.pending_token = L.generate_edit_token()
            if not d.get("submitted_at"):
                d["submitted_at"] = now
            token = s.pending_token
            record["submitted_at"] = d["submitted_at"]
            record["edit_token_hash"] = L.hash_edit_token(token, pepper)
        store.upsert(record)  # idempotent on rsvp_id: double taps/retries update the same row
    except StorageError as exc:
        s.errors = [str(exc) + " Your answers are still here, so it's safe to tap again."]
        return False
    except Exception:  # noqa: BLE001
        s.errors = ["Something went wrong while saving. Your answers are still here, so please tap again."]
        return False

    s.result = {"status": d["status"], "name": d["guest_name"], "party": L.to_int(d["party_size"], 1),
                "token": token, "updated": s.mode == "edit", "summary": summary_html(d)}
    s.edit_token, s.mode, s.step, s.errors = token, "edit", "done", []
    return True


def screen_done(settings):
    r = _s().result
    if not r:
        reset_journey()
        st.rerun()
    title = settings.shown("event_title") or "the party"
    first = (r["name"] or "friend").split()[0]
    status = r["status"]
    if status == L.ATTENDING:
        st.html(confetti_html())
        head = f"See you at dusk, {e(first)}!"
        msg = (f"Your RSVP for {r['party']} {'person' if r['party'] == 1 else 'people'} is "
               f"{'updated' if r['updated'] else 'confirmed'}. We can't wait to raise a (spooky) glass with you.")
    elif status == L.TENTATIVE:
        head = f"Got it, {e(first)}"
        msg = "We've noted you as tentative. Please update your reply once you know, using your private link below."
    else:
        head = f"Thank you, {e(first)}"
        msg = "We're sorry you can't make it, and grateful you let us know. You'll be missed."
    loc = settings.shown("venue")
    t = L.parse_time(settings.start_time)
    when = L.fmt_event_date(settings.event_date) + (f" · {L.fmt_time(t)}" if t else "")
    st.html(f"""<section class="done fade-up">
        <div class="seal">{toast_html()}</div>
        <h2 class="display">{head}</h2><p class="lede">{e(msg)}</p>
        {f'<p class="recap">{e(when)}{" · " + e(loc) if loc and status != L.DECLINED else ""}</p>' if status != L.DECLINED else ''}
        {dress_code_html(settings, reminder=True) if status != L.DECLINED else ''}
      </section>""")

    if status != L.DECLINED:
        desc = f"Hosted by {settings.shown('host_names')}" if settings.shown("host_names") else ""
        ics = L.build_ics(title, settings.event_date, settings.start_time, settings.end_time, loc, desc,
                          r["token"][:12])
        gurl = L.google_calendar_url(title, settings.event_date, settings.start_time, settings.end_time, loc, desc)
        c1, c2 = st.columns(2)
        c1.download_button("Add to calendar (.ics)", ics, file_name="halloween-sundowner.ics", mime="text/calendar",
                           width="stretch", icon=":material/event:")
        c2.link_button("Google Calendar", gurl, width="stretch", icon=":material/calendar_month:")

    with st.container(key="card_editlink"):
        st.html('<p class="person">Your private edit link</p>'
                '<p class="lede small">Save this to change your reply later. Anyone with it can edit your RSVP, so keep it to yourself.</p>')
        link = L.edit_link(settings.shown("app_url"), r["token"])
        st.code(link or r["token"], language=None)
        if link:
            wa = "https://wa.me/?text=" + quote(f"My private RSVP link for {title}: {link}")
            st.link_button("Save it to WhatsApp", wa, icon=":material/chat:", width="stretch")
        else:
            st.caption("To edit later, open the invite link, tap \"Already replied?\" and paste this code.")

    with st.expander("See your full reply"):
        st.html(r["summary"])
    c1, c2 = st.columns(2)
    c1.button("Change my reply", on_click=goto, args=("info",), width="stretch", key="done_edit")
    c2.button("Reply for someone else", on_click=reset_journey, width="stretch", key="done_new")


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
def render_guest():
    init_state()
    store = get_store()
    settings = current_settings(store)
    gate = config.gate_for(settings)
    s = _s()

    if storage_mode() == "memory":
        st.warning("Demo mode: replies are kept in memory only and are NOT saved to Google Sheets.",
                   icon=":material/science:")

    qp_token = st.query_params.get("edit")
    if qp_token:
        st.query_params.clear()  # keep the secret out of the address bar
        if store is not None:
            open_edit(qp_token, store, settings)

    # Mid-journey closure: a new RSVP can't proceed once registration closes.
    if s.step not in ("welcome", "done") and s.draft is not None:
        allowed = gate.allow_edit if s.mode == "edit" else gate.allow_new
        if not allowed or store is None:
            reset_journey()

    if s.notice and s.step == "info":
        st.info(s.notice, icon=":material/waving_hand:")

    if s.step == "welcome" or s.draft is None and s.step != "done":
        s.step = "welcome"
        screen_welcome(settings, gate, store)
        return
    if s.step == "done":
        screen_done(settings)
        return

    steps = steps_for(s.draft["status"])
    if s.step not in steps:
        s.step = steps[0]
    st.html(progress_html(steps, s.step, STEP_LABELS))
    {"info": screen_info, "food": screen_food, "drinks": screen_drinks,
     "requests": screen_requests}.get(s.step, lambda _s: None)(settings)
    if s.step == "review":
        screen_review(settings, store)
