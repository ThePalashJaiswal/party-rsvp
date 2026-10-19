# Halloween Costume Party RSVP

A mobile-first RSVP app for the house party on **Saturday, 31 October 2026** (Asia/Kolkata).
Streamlit + Google Sheets, deployed from GitHub to Streamlit Community Cloud.

- **Guests:** `https://<your-app>.streamlit.app/`
- **Hosts:** `https://<your-app>.streamlit.app/?view=host` (password protected)

---

## 0. Before you launch: what you still need to supply

The app refuses to show these as real until you set them. Nothing has been invented.

| Value | Where | Format | Required? |
|---|---|---|---|
| RSVP deadline | Host lounge → Settings, or `RSVP_DEADLINE` in `config.py` | `YYYY-MM-DD HH:MM` (IST), e.g. `2026-10-28 23:59` | **Yes.** Guests see "RSVPs open soon" until it's set |
| Event title | same | text | Recommended |
| Host names | same | text | Recommended |
| Venue (+ optional map link) | same | text / `https://` | Recommended |
| Start time (+ optional end) | same | `19:30` | Recommended (calendar invites become all-day without it) |
| Dress code | same | text | Optional |
| Host contact (shown when closed) | same | text | Recommended |
| Max people per RSVP | same | 1–20, default **4** | Check the default |
| Public app URL | same | `https://…streamlit.app` | Needed for one-tap private edit *links* (otherwise guests get a code) |
| Hero artwork | `hero.jpg` (commit it) or Settings → Hero image URL | ~2:1 landscape, < 1 MB | Optional (built-in sunset otherwise) |

---

## 1. How it works (plan summary)

**Architecture.** One Streamlit app, all Google API calls server-side.

```
streamlit_app.py   router: guest view vs ?view=host
guest_ui.py        guided RSVP screens
host_ui.py         password-protected dashboard
ui_common.py       secrets, store/settings caching, visual helpers
rsvp_logic.py      pure logic: validation, deadline gate, tokens, aggregation, .ics, CSV (unit-tested)
google_sheets.py   Sheets storage: idempotent upserts, retries, header-name column mapping
sheet_fakes.py     in-memory sheet for tests / local demo (starts empty)
config.py          ← THE place for event defaults
styles.css         invite-matched design system (crimson on grey)
```

**Guest journey.** Welcome → You (name, reply, party size, plus-one names, optional contact) → Food (per person, allergies separate) → Drinks (multi-select, "No alcohol" is exclusive, conditional follow-ups) → Extras (500-char note with counter) → Review (edit any section) → Confirmation (calendar, private edit link). Declines skip Food and Drinks. Tentative replies are recorded but never counted as confirmed.

**Google Sheet tabs.**
- `RSVPs`: one row per RSVP. Columns: `rsvp_id, submitted_at, updated_at, guest_name, email, phone, status, party_size, additional_guests, attendees_json, food_summary, dietary_restrictions, allergy_notes, preferred_dishes, alcohol_prefs, non_alcohol_prefs, beer_pref, spirits_pref, wine_pref, cocktail_pref, mixer_pref, brand_pref, other_drinks, special_requests, edit_token_hash, edit_count, last_edited_by, host_notes`. Created automatically. Columns are matched by **header name**, so reordering is safe.
- `Config`: `key | value | notes`. Host-editable event settings; non-empty values override `config.py`. **No secrets.**
- `Invitees` (optional, you create it): names in column A under a header. Enables the "Not yet replied" count.

**Deadline.** `auto` mode accepts RSVPs only while now (IST) < deadline. A placeholder, badly formatted, or after-the-party deadline is treated as *not configured*: RSVPs stay closed. The gate is re-checked against a **fresh** read of the Config tab at the moment of submit, so it's enforced server-side, not just by hiding buttons. Hosts can force `open` (reopen/extend) or `closed`. Guest edits after the deadline are off unless "Let guests edit after registration closes" is ticked.

**Security model.**
- Guests never see sheet data. The guest view reads only the edit-token-hash column (server-side) when someone opens their own RSVP.
- Edit tokens: 192-bit random, shown once to the guest; only an HMAC-SHA256 hash (keyed with `token_pepper`) is stored. Tokens are re-verified on every save. Names, emails, row numbers or IDs never grant access.
- The token is stripped from the address bar as soon as it's used.
- Host dashboard: password from secrets, constant-time compare, 12-hour session, process-wide lockout after 8 failures in 10 minutes. Hosts can reissue a guest's lost link (old one dies instantly).
- Values are written with `RAW` input (no formulas evaluated); CSV exports neutralise `= + - @` cells. All user text is HTML-escaped.
- Food export has no contact details or alcohol data; bar export has no contact details; contact in the guest list is opt-in.

**Google Sheets trade-offs (be honest with yourself).** Sheets is not a database. Appends are atomic, so simultaneous new RSVPs don't overwrite each other, and every write looks up `rsvp_id` first, so retries and double taps update rather than duplicate. But there are no transactions or row locks: **don't sort the RSVPs tab in place while RSVPs are coming in** (use a filter view). Two different phones submitting for the same person still create two rows; the dashboard flags likely duplicates by name. API quota (~60 reads/min/user) is ample for a house party.

---

## 2. Deploy, in this order

### Step 1: Google Cloud service account (≈10 min)
1. Go to <https://console.cloud.google.com/> → project picker → **New project** (e.g. `party-rsvp`).
2. **APIs & Services → Library →** search **Google Sheets API → Enable**. (Drive API is not needed: the app opens the sheet by ID.)
3. **APIs & Services → Credentials → Create credentials → Service account.** Name it `rsvp-bot`. Skip the optional role and user steps.
4. Open the service account → **Keys → Add key → Create new key → JSON**. A file downloads.
   Treat it like a password: don't email it, don't put it in the repo folder, delete it after Step 4.
5. Copy the service account's email (`rsvp-bot@….iam.gserviceaccount.com`).

### Step 2: The spreadsheet
1. Create a new Google Sheet (e.g. "Party RSVPs").
2. **Share** → paste the service-account email → **Editor** → untick "Notify" → Share. Share nothing else with it.
3. Copy the ID from the URL: `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
4. You don't need to create tabs or headers; the app creates `RSVPs` and `Config` on first run. Optionally add an `Invitees` tab with `name` in A1 and one invitee per row.

### Step 3: GitHub
1. Create a **private** repository (e.g. `party-rsvp`).
2. Upload everything in this folder **except** any `secrets.toml` or `.json` key (the `.gitignore` already blocks them).
3. Optionally add `hero.jpg` (your wife's artwork).
4. Check on GitHub that there is **no** `.streamlit/secrets.toml` and no `.json` file in the repo. Only `secrets.toml.example` should be there.

### Step 4: Streamlit Community Cloud
1. <https://share.streamlit.io> → **Create app → Deploy a public app from GitHub**.
2. Repo: `party-rsvp`, branch `main`, main file **`streamlit_app.py`**. Optionally pick a custom subdomain.
3. **Advanced settings → Secrets**: paste the structure from `.streamlit/secrets.toml.example` and fill it in:
   - `spreadsheet_key`: from Step 2.
   - `token_pepper`: run `python -c "import secrets; print(secrets.token_urlsafe(48))"`. **Never change it after launch** (it would invalidate every guest's edit link).
   - `[admin] password`: 4+ random words.
   - `[gcp_service_account]`: copy each field from the JSON key. Keep `private_key` on one line with the literal `\n` sequences.
   - `storage = "sheets"`.
4. **Deploy.** Then delete the downloaded JSON key from your computer.

### Step 5: Configure & test end to end
1. Open `https://<app>.streamlit.app/?view=host` → log in.
2. **Settings:** fill in title, hosts, venue, start time, dress code, **deadline**, host contact, and the **public app URL** → Save. Confirm the Config tab in your sheet now has these rows.
3. Open the public URL **on your phone** in a private window. Submit a test RSVP with a plus-one, an allergy and "No alcohol". Save the edit link.
4. Check the `RSVPs` tab: one row, `edit_token_hash` is a 64-char hex string (not your link's code).
5. Open your edit link in another browser → change to "Can't make it" → save. The **same** row updates and `edit_count` rises.
6. Dashboard: confirm counts, Food tab shows the allergy, exports download.
7. Privacy checks: open `/?view=host` logged out (only the password box shows); type a wrong password (rejected).
8. Deadline check: in Settings set **Registration → Force closed** → reload the guest page (should say RSVPs have closed) → set back to **Follow deadline**.
9. Delete your test row(s) from the sheet.

### Step 6: Share
Put the public URL (or a QR code of it) on the invitation creative. Any QR generator works; test the code with two different phones before printing. **Don't** share the `?view=host` link.

---

## 3. Everyday customisation

| Change | Easiest place |
|---|---|
| Title, hosts, venue, times, dress code, deadline, party-size limit, open/closed, edit policy, headline, hero URL, app URL | Host lounge → Settings (writes to the Config tab) |
| Defaults if the Config tab is empty | `config.py` (top of file) |
| Hero artwork | commit `hero.jpg` |
| Colours & fonts | `:root` tokens at the top of `styles.css` |
| Food/drink options | lists at the top of `rsvp_logic.py` |

Extending the deadline: change the deadline in Settings. Reopening after it passed: **Force open**. Closing early: **Force closed**.

---

## 4. Local development

```bash
pip install -r requirements-dev.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # git-ignored; fill it in
streamlit run streamlit_app.py
```

For a no-credentials demo, set `storage = "memory"` in your local `secrets.toml`, plus a `token_pepper` and admin password. A banner warns that nothing is saved. **Never use memory mode in production.**

### Tests

```bash
pytest -q
```

- `tests/test_validation.py`: validation, allergies vs diet, no-alcohol rule, tokens, settings, CSV safety.
- `tests/test_rsvp_logic.py`: deadline gate (incl. IST conversion, placeholders, host overrides), KPI/food/bar aggregation, calendar, and the storage layer against a mocked sheet (idempotency, lost-response retries, permanent failures, reordered columns, rows moving, token isolation).
- `tests/test_app_flow.py`: drives the real Streamlit UI (AppTest): full attend → edit-by-link flow, decline path, no-alcohol exclusivity, deadline closing mid-journey, invalid tokens, dashboard login and live counts.

The live Google integration can only be tested with your credentials: follow Step 5.

---

## 5. Known limits
- Drink preferences are per RSVP, not per person, so bar counts show "RSVPs" and an upper bound of "people in those RSVPs". These are preferences, not purchase quantities.
- "Not yet replied" matches invitee names to RSVP names; spelling differences cause misses.
- Uploading a hero image from the dashboard isn't offered because Community Cloud's disk is wiped on restart; commit the file or use a URL.
- Want stronger host auth than a shared password? Streamlit's `st.login` with Google OIDC is a drop-in upgrade for `login_screen()` in `host_ui.py`.
