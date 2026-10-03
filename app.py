"""Health tracker -- beta.

Design rules this app is built around:
  * Logging is retrospective. Every panel opens on a date you can change.
  * Time of day is always prefilled from the clock and always editable.
  * Nothing is typed that can be tapped.
  * Every panel writes into one `events` table (see schema.sql).
"""

from __future__ import annotations

import hmac
import uuid
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd
import streamlit as st

import config as C
from db import _secret, get_db

st.set_page_config(page_title=C.APP_TITLE, page_icon="✚",
                   layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; padding-bottom: 4rem; max-width: 560px;}
  div[data-testid="stButton"] button {min-height: 2.9rem; font-size: 1rem;}
  div[data-testid="stButton"] button p {font-size: 1rem; margin: 0;}
  header[data-testid="stHeader"] {height: 0;}
  .ref {color: #888; font-size: 0.78rem; line-height: 1.5;}
  .recap {color: #666; font-size: 0.85rem;}
  hr {margin: 0.6rem 0;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Passcode gate
# ---------------------------------------------------------------------------
# Active only when APP_PASSCODE is set in secrets, so local runs stay open.
# Three ways in: a remembered device (cookie), the passcode in the URL (?k=...),
# or typing it. The Supabase key never leaves the server either way: Streamlit
# runs this Python server-side and sends only rendered UI to the browser.
#
# "Remember this device" stores a cookie rather than relying on the URL, because
# iPhone home-screen web apps launch the site's start address and drop ?k=.
# The cookie holds an HMAC of the passcode, never the passcode itself, so
# changing APP_PASSCODE automatically locks out every remembered device.

REMEMBER_COOKIE = "tracker_device"
REMEMBER_DAYS = 400   # browsers cap cookie lifetimes around here anyway


def _passcode_ok(supplied: str | None, secret: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied.strip().encode(), secret.encode())


def _device_token(secret: str) -> str:
    return hmac.new(secret.encode(), b"tracker:remember-this-device", "sha256").hexdigest()


def _write_cookie(value: str, max_age: int):
    # Runs in the app's own page, so the cookie belongs to the app's origin.
    st.html("<script>document.cookie = "
            f"'{REMEMBER_COOKIE}={value}; path=/; max-age={max_age}; SameSite=Lax; Secure';"
            "</script>", unsafe_allow_javascript=True)


def require_passcode():
    secret = _secret("APP_PASSCODE")
    if not secret:
        return
    ss = st.session_state

    if ss.get("_authed"):
        if ss.pop("_remember_pending", False):
            _write_cookie(_device_token(secret), REMEMBER_DAYS * 86400)
        return

    if ss.pop("_forget_pending", False):
        _write_cookie("", 0)
    elif not ss.get("_ignore_cookie"):
        try:
            cookie = st.context.cookies.get(REMEMBER_COOKIE)
        except Exception:          # no browser context (e.g. headless tests)
            cookie = None
        if isinstance(cookie, str) and cookie and \
                hmac.compare_digest(cookie.encode(), _device_token(secret).encode()):
            ss._authed = True
            return

    if _passcode_ok(st.query_params.get("k"), secret):
        ss._authed = True
        return

    st.markdown("## 🔒 Tracker")
    with st.form("passcode"):
        pw = st.text_input("Passcode", type="password")
        remember = st.checkbox("Remember this device", value=True)
        if st.form_submit_button("Unlock", type="primary", width='stretch'):
            if _passcode_ok(pw, secret):
                ss._authed = True
                ss._remember_pending = remember
                st.rerun()
            st.error("That passcode didn't match.")
    st.stop()


def lock_device():
    """Forget this device and return to the lock screen."""
    ss = st.session_state
    ss._authed = False
    ss._forget_pending = True
    ss._ignore_cookie = True      # this session's cookie snapshot is now stale
    st.rerun()


require_passcode()


# ---------------------------------------------------------------------------
# State + helpers
# ---------------------------------------------------------------------------

@st.cache_resource
def _db():
    return get_db()


db = _db()

PANELS = [
    ("food", "🍽️  Food & drink"),
    ("alcohol", "🍷  Alcohol"),
    ("symptom", "🩹  Symptoms"),
    ("supplement", "💊  Supplements"),
    ("habit", "✅  Habits"),
]

st.session_state.setdefault("panel", None)
st.session_state.setdefault("log_date", date.today())
st.session_state.setdefault("alc_lines", [])
st.session_state.setdefault("sym_lines", [])
st.session_state.setdefault("food_lines", [])
st.session_state.setdefault("flash", None)


def go(panel: str | None):
    st.session_state.panel = panel
    st.session_state.alc_lines = []
    st.session_state.sym_lines = []
    st.session_state.food_lines = []
    st.rerun()


def flash(msg: str):
    st.session_state.flash = msg


def show_flash():
    if st.session_state.flash:
        st.success(st.session_state.flash)
        st.session_state.flash = None


def ref_rows(table: str):
    """Reference lists, cached per rerun so panels don't re-query on every tap."""
    key = f"_ref_{table}"
    if key not in st.session_state:
        st.session_state[key] = db.ref_list(table)
    return st.session_state[key]


def bust_refs():
    for t in ("supplements", "supplement_regimens", "symptom_types", "habits"):
        st.session_state.pop(f"_ref_{t}", None)


def slot_default_time(slot: str) -> dtime:
    """Prefilled time for an eating occasion. Snacks mean 'now'; the three
    meals use the times set in Settings, falling back to the defaults."""
    if slot == "snack":
        n = datetime.now()
        return dtime(n.hour, n.minute)
    raw = db.setting_get(C.MEAL_TIME_SETTING.format(slot=slot),
                         C.DEFAULT_MEAL_TIMES[slot])
    try:
        hh, mm = str(raw).split(":")[:2]
        return dtime(int(hh), int(mm))
    except (ValueError, TypeError):
        hh, mm = C.DEFAULT_MEAL_TIMES[slot].split(":")
        return dtime(int(hh), int(mm))


def fmt_time(value) -> str:
    """'18:00' or a time object -> '6:00 pm'. Blank for missing times."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            hh, mm = value.split(":")[:2]
            value = dtime(int(hh), int(mm))
        except ValueError:
            return value
    return value.strftime("%I:%M %p").lstrip("0").lower()


def day_events(d: date, category: str) -> list[dict]:
    return db.events_fetch(start=d, end=d, category=category)


def replace_day(category: str, d: date, rows: list[dict]):
    """Idempotent save: clear this category+day, then write the new rows."""
    for e in day_events(d, category):
        db.event_delete(e["id"])
    db.events_insert(rows)


def header(title: str):
    c1, c2 = st.columns([1, 3.4])
    with c1:
        if st.button("← Back", width='stretch'):
            go(None)
    with c2:
        st.markdown(f"### {title}")

    d = st.session_state.log_date
    c1, c2, c3 = st.columns([1, 2.2, 1])
    with c1:
        if st.button("◀", width='stretch', help="Previous day"):
            st.session_state.log_date = d - timedelta(days=1)
            st.rerun()
    with c2:
        picked = st.date_input("date", value=d, label_visibility="collapsed",
                               max_value=date.today(), format="MM/DD/YYYY")
        if picked != d:
            st.session_state.log_date = picked
            st.rerun()
    with c3:
        nxt = d + timedelta(days=1)
        if st.button("▶", width='stretch', disabled=nxt > date.today(),
                     help="Next day"):
            st.session_state.log_date = nxt
            st.rerun()
    d = st.session_state.log_date
    label = "Today" if d == date.today() else (
        "Yesterday" if d == date.today() - timedelta(days=1) else d.strftime("%A"))
    st.markdown(f"<div class='recap' style='text-align:center'>{label}</div>",
                unsafe_allow_html=True)
    st.divider()
    show_flash()
    return d


def bucket_picker(key: str, default: str) -> str:
    """All four options visible, prefilled from the clock."""
    idx = C.TIME_BUCKETS.index(default) if default in C.TIME_BUCKETS else 2
    return st.radio("time of day", C.TIME_BUCKETS, index=idx, key=key,
                    horizontal=True, label_visibility="collapsed")


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def home():
    st.markdown("## Today")
    show_flash()

    today = date.today()
    counts: dict[str, int] = {}
    for e in db.events_fetch(start=today, end=today):  # one query, grouped here
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    bits = [f"{lbl.split('  ')[1].lower()}: {counts[cat]}"
            for cat, lbl in PANELS if counts.get(cat)]
    st.markdown(f"<div class='recap'>{' · '.join(bits) if bits else 'Nothing logged yet today.'}</div>",
                unsafe_allow_html=True)
    st.write("")

    for cat, label in PANELS:
        with st.container(border=True):
            if st.button(label, key=f"panel_{cat}", width='stretch'):
                st.session_state.log_date = date.today()
                go(cat)

    st.write("")
    if st.button("⚙️  Settings & export", width='stretch'):
        go("settings")
    st.caption(f"storage: {db.kind}")
    if _secret("APP_PASSCODE") and st.button("🔒 Lock this device", key="lock_device"):
        lock_device()


# ---------------------------------------------------------------------------
# Alcohol
# ---------------------------------------------------------------------------

def panel_alcohol():
    d = header("Alcohol")
    st.caption("Tap a drink. Tap it twice for two different times of day.")

    cols = st.columns(3)
    for i, t in enumerate(C.DRINK_TYPES):
        with cols[i % 3]:
            if st.button(t, key=f"drink_{t}", width='stretch'):
                st.session_state.alc_lines.append(
                    {"uid": str(uuid.uuid4())[:8], "type": t,
                     "qty": 1, "bucket": C.current_bucket()}
                )
                st.rerun()

    lines = st.session_state.alc_lines
    if lines:
        st.write("")
        for line in list(lines):
            with st.container(border=True):
                c1, c2, c3 = st.columns([2.1, 1.7, 0.8])
                c1.markdown(f"**{line['type']}**")
                line["qty"] = c2.number_input(
                    "drinks", min_value=1, max_value=30, step=1,
                    value=int(line["qty"]), key=f"q_{line['uid']}",
                    label_visibility="collapsed")
                if c3.button("✕", key=f"x_{line['uid']}", width='stretch'):
                    lines.remove(line)
                    st.rerun()
                line["bucket"] = bucket_picker(f"b_{line['uid']}", line["bucket"])

        total = sum(int(l["qty"]) for l in lines)
        if st.button(f"Save {total} drink{'s' if total != 1 else ''}",
                     type="primary", width='stretch'):
            db.events_insert([
                {"event_date": d, "time_bucket": l["bucket"], "category": "alcohol",
                 "item": l["type"], "quantity": int(l["qty"]), "unit": "drinks"}
                for l in lines
            ])
            st.session_state.alc_lines = []
            flash(f"Logged {total} drink{'s' if total != 1 else ''}.")
            st.rerun()

    st.markdown(
        "<div class='ref'><b>1 standard drink =</b> " +
        " · ".join(f"{k} {v}" for k, v in C.STANDARD_DRINK_REFERENCE.items()) +
        "<br>Log a bigger pour as more drinks — a 10 oz glass of wine is 2.</div>",
        unsafe_allow_html=True)

    existing = day_events(d, "alcohol")
    if existing:
        st.divider()
        st.markdown("**Already logged this day**")
        for e in existing:
            c1, c2 = st.columns([4, 0.8])
            c1.write(f"{int(e['quantity'])} × {e['item']} — {e['time_bucket']}")
            if c2.button("✕", key=f"del_{e['id']}", width='stretch'):
                db.event_delete(e["id"])
                st.rerun()


# ---------------------------------------------------------------------------
# Food
# ---------------------------------------------------------------------------

def panel_food():
    d = header("Food & drink")
    st.caption("Pick the occasion, then check the time.")

    rev = st.session_state.setdefault("food_rev", 0)
    slot = st.session_state.setdefault("food_slot_sel", "snack")

    cols = st.columns(4)
    for i, s in enumerate(C.MEAL_SLOTS):
        with cols[i]:
            if st.button(s, key=f"slot_{i}", width='stretch',
                         type="primary" if s == slot else "secondary"):
                st.session_state.food_slot_sel = s
                st.session_state.food_rev = rev + 1   # re-prefill the time
                st.rerun()
    slot = st.session_state.food_slot_sel

    when = st.time_input("time", value=slot_default_time(slot),
                         key=f"food_time_{rev}", step=300,
                         label_visibility="collapsed")
    text = st.text_area("what you ate", key=f"food_text_{rev}", height=110,
                        placeholder="oatmeal with blueberries and peanut butter, black coffee",
                        label_visibility="collapsed")

    if st.button("Save", type="primary", width='stretch',
                 disabled=not text.strip()):
        db.events_insert([{
            "event_date": d, "event_time": when,
            "time_bucket": C.current_bucket(datetime.combine(d, when)),
            "category": "food", "item": text.strip(),
            "detail": {"slot": slot},
        }])
        st.session_state.food_rev = rev + 1   # fresh widget keys == cleared box
        flash(f"Logged {slot} at {fmt_time(when)}.")
        st.rerun()

    existing = day_events(d, "food")
    if existing:
        st.divider()
        st.markdown("**Already logged this day**")
        for e in existing:
            with st.container(border=True):
                c1, c2 = st.columns([4, 0.8])
                slot_lbl = (e.get("detail") or {}).get("slot", "")
                when = fmt_time(e.get("event_time"))       # blank on pre-time rows
                head = f"**{slot_lbl}**" + (f" · {when}" if when else "")
                c1.markdown(f"{head}  \n{e['item']}")
                if c2.button("✕", key=f"del_{e['id']}", width='stretch'):
                    db.event_delete(e["id"])
                    st.rerun()



# ---------------------------------------------------------------------------
# Symptoms
# ---------------------------------------------------------------------------

def panel_symptoms():
    d = header("Symptoms")
    types = ref_rows("symptom_types")
    st.caption("Tap a symptom, set severity, and pick every time of day it applied.")

    def add_line(name: str):
        st.session_state.sym_lines.append(
            {"uid": str(uuid.uuid4())[:8], "name": name, "severity": 3,
             "buckets": [C.current_bucket()], "note": ""})

    if types:
        cols = st.columns(3)
        for i, t in enumerate(types):
            with cols[i % 3]:
                if st.button(t["name"], key=f"sym_{t['id']}", width='stretch'):
                    add_line(t["name"])
                    st.rerun()

    with st.expander("➕ New symptom", expanded=not types):
        rev = st.session_state.setdefault("sym_new_rev", 0)
        new_name = st.text_input("Symptom name", key=f"sym_new_{rev}",
                                 placeholder="e.g. brain fog")
        if st.button("Add symptom", key="sym_new_add", width='stretch',
                     disabled=not new_name.strip()):
            name = new_name.strip()
            known = {t["name"].lower(): t["name"] for t in types}
            if name.lower() not in known:
                db.ref_upsert("symptom_types", {"name": name, "active": True,
                                                "sort_order": len(types)})
                bust_refs()
            add_line(known.get(name.lower(), name))
            st.session_state.sym_new_rev = rev + 1   # clears the text box
            st.rerun()

    lines = st.session_state.sym_lines
    if lines:
        st.write("")
        for line in list(lines):
            with st.container(border=True):
                c1, c2 = st.columns([3.4, 0.8])
                c1.markdown(f"**{line['name']}**")
                if c2.button("✕", key=f"sx_{line['uid']}", width='stretch'):
                    lines.remove(line)
                    st.rerun()
                line["severity"] = st.select_slider(
                    "severity", options=[1, 2, 3, 4, 5],
                    value=line["severity"], key=f"sv_{line['uid']}",
                    format_func=lambda v: C.SEVERITY_LABELS[v].split(" · ")[1],
                    label_visibility="collapsed")
                picked = st.pills("time of day", C.TIME_BUCKETS, selection_mode="multi",
                                  default=line["buckets"], key=f"sb_{line['uid']}",
                                  label_visibility="collapsed")
                line["buckets"] = [b for b in C.TIME_BUCKETS if b in (picked or [])]
                line["note"] = st.text_input(
                    "note", value=line["note"], key=f"sn_{line['uid']}",
                    placeholder="note (optional)", label_visibility="collapsed")

        missing = [l["name"] for l in lines if not l["buckets"]]
        if missing:
            st.warning("Pick at least one time of day for: " + ", ".join(missing))
        if st.button(f"Save {len(lines)} symptom{'s' if len(lines) != 1 else ''}",
                     type="primary", width='stretch', disabled=bool(missing)):
            rows = []
            for l in lines:
                # One row per time of day keeps time_bucket queryable alongside
                # alcohol and food; entry_id ties the rows back into one entry.
                entry_id = str(uuid.uuid4())
                for b in l["buckets"]:
                    rows.append({
                        "event_date": d, "time_bucket": b, "category": "symptom",
                        "item": l["name"], "severity": int(l["severity"]),
                        "note": l["note"].strip() or None,
                        "detail": {"entry_id": entry_id, "buckets": l["buckets"]},
                    })
            db.events_insert(rows)
            st.session_state.sym_lines = []
            flash("Symptoms logged.")
            st.rerun()

    existing = day_events(d, "symptom")
    if existing:
        groups: dict[str, list[dict]] = {}
        for e in existing:
            gid = (e.get("detail") or {}).get("entry_id") or e["id"]
            groups.setdefault(gid, []).append(e)
        st.divider()
        st.markdown("**Already logged this day**")
        for gid, rows in groups.items():
            first = rows[0]
            seen = {r["time_bucket"] for r in rows}
            when = ", ".join(b for b in C.TIME_BUCKETS if b in seen)
            note = f" — {first['note']}" if first.get("note") else ""
            c1, c2 = st.columns([4, 0.8])
            c1.write(f"{first['item']} · severity {first['severity']} · {when}{note}")
            if c2.button("✕", key=f"del_{gid}", width='stretch'):
                for r in rows:
                    db.event_delete(r["id"])
                st.rerun()


# ---------------------------------------------------------------------------
# Supplements
# ---------------------------------------------------------------------------

# Rows the Save button owns for a day. One-off "extra" rows are written and
# removed individually, so a save never wipes them.
_SUP_SAVE_KINDS = {"skipped", "dose_changed", "taken", "exception", "day_confirmed"}


def _sup_meta(r: dict) -> str:
    bits = [r.get("frequency"), r.get("brand")]
    return " · ".join(b for b in bits if b)


def _add_supplement(d: date, in_force: set, prefix: str):
    """Submit callback: runs before the page re-renders, so the new item shows
    up in the list immediately without an explicit rerun."""
    ss = st.session_state
    catalog = ref_rows("supplements")
    pick, typed = ss[f"{prefix}_pick"], ss[f"{prefix}_name"].strip()
    dose, unit = ss[f"{prefix}_dose"], ss[f"{prefix}_unit"].strip()
    usual, freq, brand = ss[f"{prefix}_usual"], ss[f"{prefix}_freq"], ss[f"{prefix}_brand"].strip()

    name = typed if pick == "— new supplement —" else pick
    if not name:
        ss["_sup_add_warning"] = "Give the supplement a name."
        return
    match = next((s for s in catalog if s["name"].lower() == name.lower()), None)
    if usual and match and match["id"] in in_force:
        ss["_sup_add_warning"] = f"{match['name']} is already in your usual list."
        return
    sid = match["id"] if match else db.ref_upsert("supplements", {
        "name": name, "brand": brand or None, "sort_order": len(catalog)})
    if usual:
        db.ref_upsert("supplement_regimens", {
            "supplement_id": sid, "dose": dose or None, "unit": unit or None,
            "frequency": freq, "start_date": d.isoformat(), "end_date": None,
            "status": "active", "reason": None, "notes": None})
        flash(f"{name} added to your usual list from {d:%b} {d.day}.")
    else:
        db.events_insert([{
            "event_date": d, "category": "supplement", "item": name,
            "quantity": dose or None, "unit": unit or None,
            "detail": {"kind": "extra", "supplement_id": sid}}])
        flash(f"Logged {name} for this day.")
    bust_refs()


def supplement_add_form(d: date, regimens: list[dict], *, usual_default: bool, key: str):
    catalog = ref_rows("supplements")
    in_force = {r["supplement_id"] for r in regimens}
    p = f"supadd_{key}"
    warning = st.session_state.pop("_sup_add_warning", None)
    if warning:
        st.warning(warning)
    with st.form(p, clear_on_submit=True):
        st.selectbox("Supplement", ["— new supplement —"] + [s["name"] for s in catalog],
                     key=f"{p}_pick")
        st.text_input("Name (if new)", placeholder="Vitamin C", key=f"{p}_name")
        c1, c2 = st.columns(2)
        c1.number_input("Dose", min_value=0.0, step=0.5, value=1.0, key=f"{p}_dose")
        c2.text_input("Unit", placeholder="capsule, mg, scoop", key=f"{p}_unit")
        st.checkbox("Part of my usual intake", value=usual_default, key=f"{p}_usual",
                    help="Usual supplements are assumed taken every day they're due.")
        st.selectbox("How often (if usual)", C.SUPPLEMENT_FREQUENCIES, key=f"{p}_freq")
        st.text_input("Brand (optional)", key=f"{p}_brand")
        st.form_submit_button("Add", width='stretch', on_click=_add_supplement,
                              args=(d, in_force, p))


def panel_supplements():
    d = header("Supplements")
    regimens = db.regimens_on(d)

    if not regimens:
        st.markdown("#### What do you usually take?")
        st.caption("Add each supplement you take regularly and how often. From then "
                   "on they're assumed taken unless you say otherwise.")
        supplement_add_form(d, regimens, usual_default=True, key="setup")
        return

    daily = [r for r in regimens if C.is_daily(r.get("frequency"))]
    occasional = [r for r in regimens if not C.is_daily(r.get("frequency"))]

    # What was already recorded for this day, keyed by regimen id (older rows by name).
    existing = day_events(d, "supplement")
    skipped, dose_seen, taken, extras, stored = set(), {}, set(), [], set()
    for e in existing:
        det = e.get("detail") or {}
        kind, k = det.get("kind"), det.get("regimen_id") or e.get("item")
        if kind == "skipped" or (kind == "exception" and det.get("exception") == "skipped"):
            skipped.add(k)
        elif kind == "dose_changed":
            dose_seen[k] = e.get("quantity")
        elif kind == "taken":
            taken.add(k)
            if e.get("quantity") is not None:
                dose_seen[k] = e["quantity"]
        elif kind == "extra" or (kind == "exception" and det.get("exception") == "extra"):
            extras.append(e)
        if kind in ("skipped", "dose_changed", "taken"):
            stored.add((kind, k, e.get("quantity") if kind != "skipped" else None))

    def has(s, r):
        return r["id"] in s or r["name"] in s

    def recorded_dose(r):
        return dose_seen.get(r["id"], dose_seen.get(r["name"]))

    st.caption("Your usual supplements are assumed taken. Turn off anything you "
               "skipped or change a dose, then save. If nothing changed, there's "
               "nothing to do.")

    rows, wanted = [], set()

    def dose_row(r, k, enabled):
        base = float(r["dose"]) if r.get("dose") is not None else 1.0
        prior = recorded_dose(r)
        return st.number_input(r.get("unit") or "dose", min_value=0.0, step=0.5,
                               value=float(prior) if prior is not None else base,
                               key=f"sd_{k}", disabled=not enabled), base

    if daily:
        st.markdown("**Taken today**")
        for r in daily:
            k = f"{d}_{r['id']}"
            with st.container(border=True):
                c1, c2 = st.columns([3, 1.6])
                on = c1.toggle(r["name"], value=not has(skipped, r), key=f"st_{k}")
                c1.caption(_sup_meta(r))
                with c2:
                    new_dose, base = dose_row(r, k, on)
            if not on:
                rows.append({"item": r["name"], "detail": {"kind": "skipped", "regimen_id": r["id"]}})
                wanted.add(("skipped", r["id"], None))
            elif abs(new_dose - base) > 1e-9:
                rows.append({"item": r["name"], "quantity": new_dose, "unit": r.get("unit"),
                             "detail": {"kind": "dose_changed", "regimen_id": r["id"],
                                        "usual_dose": base}})
                wanted.add(("dose_changed", r["id"], new_dose))

    if occasional:
        st.markdown("**Not daily — turn on if taken today**")
        for r in occasional:
            k = f"{d}_{r['id']}"
            with st.container(border=True):
                c1, c2 = st.columns([3, 1.6])
                on = c1.toggle(r["name"], value=has(taken, r), key=f"st_{k}")
                c1.caption(_sup_meta(r))
                with c2:
                    new_dose, _ = dose_row(r, k, on)
            if on:
                rows.append({"item": r["name"], "quantity": new_dose, "unit": r.get("unit"),
                             "detail": {"kind": "taken", "regimen_id": r["id"],
                                        "frequency": r.get("frequency")}})
                wanted.add(("taken", r["id"], new_dose))

    if extras:
        st.markdown("**Also taken this day**")
        for e in extras:
            c1, c2 = st.columns([4, 0.8])
            amt = f" · {e['quantity']:g} {e.get('unit') or ''}".rstrip() if e.get("quantity") else ""
            c1.write(f"{e['item']}{amt}")
            if c2.button("✕", key=f"del_{e['id']}", width='stretch'):
                db.event_delete(e["id"])
                st.rerun()

    # Compare against what's stored by regimen id, so older name-keyed rows
    # simply read as "changed" and get rewritten in the new shape on save.
    changed = wanted != stored
    if st.button("Save changes", type="primary", width='stretch', disabled=not changed):
        for e in existing:
            if (e.get("detail") or {}).get("kind") in _SUP_SAVE_KINDS:
                db.event_delete(e["id"])
        db.events_insert([{**row, "event_date": d, "category": "supplement"} for row in rows])
        n_skip = sum(1 for r in rows if r["detail"]["kind"] == "skipped")
        n_dose = sum(1 for r in rows if r["detail"]["kind"] == "dose_changed")
        bits = [f"{n_skip} skipped" if n_skip else "", f"{n_dose} dose change{'s' if n_dose != 1 else ''}" if n_dose else ""]
        flash("Saved — " + (", ".join(b for b in bits if b) or "your usual list stands for this day") + ".")
        st.rerun()
    if not changed:
        st.caption("No changes — your usual list counts for this day.")

    with st.expander("➕ Add a supplement"):
        supplement_add_form(d, regimens, usual_default=False, key="day")


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

def panel_habits():
    d = header("Habits")
    habits = ref_rows("habits")
    if not habits:
        st.info("No habits defined yet. Add them in Settings.")
        return

    existing = {e["item"]: e for e in day_events(d, "habit")}
    st.caption("Set the day, then save. Saving again replaces the day.")

    values: dict[str, float] = {}
    for h in habits:
        prior = existing.get(h["name"])
        with st.container(border=True):
            if h["kind"] == "binary":
                done = st.toggle(h["name"],
                                 value=bool(prior and prior["quantity"]),
                                 key=f"hb_{h['id']}")
                values[h["name"]] = 1 if done else 0
            else:
                c1, c2 = st.columns([2.2, 2.0])
                unit = h.get("unit") or ""
                target = f" · goal {h['target']:g}" if h.get("target") else ""
                c1.markdown(f"**{h['name']}**  \n<span class='ref'>{unit}{target}</span>",
                            unsafe_allow_html=True)
                values[h["name"]] = c2.number_input(
                    h["name"], min_value=0.0, step=1.0,
                    value=float(prior["quantity"]) if prior else 0.0,
                    key=f"hc_{h['id']}", label_visibility="collapsed")

    if st.button("Save day", type="primary", width='stretch'):
        by_name = {h["name"]: h for h in habits}
        rows = [{
            "event_date": d, "time_bucket": None, "category": "habit",
            "item": name, "quantity": val,
            "unit": by_name[name].get("unit") or ("done" if by_name[name]["kind"] == "binary" else None),
            "detail": {"kind": by_name[name]["kind"]},
        } for name, val in values.items()]
        replace_day("habit", d, rows)
        flash("Habits saved.")
        st.rerun()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _seed_defaults():
    if not db.ref_list("symptom_types"):
        for i, name in enumerate(C.DEFAULT_SYMPTOMS):
            db.ref_upsert("symptom_types", {"name": name, "sort_order": i, "active": True})
    if not db.ref_list("habits"):
        for i, (name, kind, unit) in enumerate(C.DEFAULT_HABITS):
            db.ref_upsert("habits", {"name": name, "kind": kind, "unit": unit,
                                     "sort_order": i, "active": True})
    bust_refs()


def panel_settings():
    c1, c2 = st.columns([1, 3.4])
    with c1:
        if st.button("← Back", width='stretch'):
            go(None)
    with c2:
        st.markdown("### Settings")
    show_flash()

    tabs = st.tabs(["Supplements", "Meal times", "Symptoms", "Habits", "Export"])

    # -- supplements --------------------------------------------------------
    with tabs[0]:
        st.caption("A regimen is a stretch of time you intended to take "
                   "something a certain way. Stopping and restarting later "
                   "makes a new regimen, so separate trials stay separable.")
        catalog = {s["id"]: s for s in ref_rows("supplements")}
        regimens = ref_rows("supplement_regimens")

        for r in regimens:
            s = catalog.get(r["supplement_id"])
            if not s:
                continue
            with st.container(border=True):
                c1, c2 = st.columns([4, 0.8])
                dose = f"{r['dose']:g} {r.get('unit') or ''}".strip() if r.get("dose") else ""
                meta = " · ".join(x for x in [dose, r.get("frequency"), s.get("brand"),
                                              s.get("form")] if x)
                span = f"{r['start_date']} → {r.get('end_date') or 'ongoing'}"
                note = f"  \n{r['notes']}" if r.get("notes") else ""
                c1.markdown(
                    f"**{s['name']}** · `{r['status']}`  \n"
                    f"<span class='ref'>{meta}<br>{span}</span>{note}",
                    unsafe_allow_html=True)
                if c2.button("✕", key=f"dr_{r['id']}", width='stretch'):
                    db.ref_delete("supplement_regimens", r["id"])
                    bust_refs()
                    st.rerun()
                if r["status"] == "active":
                    if st.button("Stop this regimen", key=f"stop_{r['id']}",
                                 width='stretch'):
                        db.ref_upsert("supplement_regimens",
                                      {**r, "status": "stopped",
                                       "end_date": date.today().isoformat()})
                        bust_refs()
                        st.rerun()

        with st.expander("Add a supplement / regimen"):
            with st.form("add_regimen", clear_on_submit=True):
                names = ["— new supplement —"] + [s["name"] for s in catalog.values()]
                pick = st.selectbox("Supplement", names)
                name = st.text_input("New name", placeholder="Magnesium (Glycinate)")
                c1, c2 = st.columns(2)
                brand = c1.text_input("Brand", placeholder="Pure Encapsulations")
                form = c2.selectbox("Form", [""] + C.SUPPLEMENT_FORMS)
                c1, c2, c3 = st.columns(3)
                dose = c1.number_input("Dose", min_value=0.0, step=0.5, value=1.0)
                unit = c2.text_input("Unit", placeholder="capsule, mg")
                freq = c3.selectbox("Frequency", C.SUPPLEMENT_FREQUENCIES)
                c1, c2 = st.columns(2)
                start = c1.date_input("Start date", value=date.today())
                status = c2.selectbox("Status", C.REGIMEN_STATUSES)
                reason = st.text_input("Reason", placeholder="why you take it")
                notes = st.text_input("Notes", placeholder="5000 IU D3 with K2")

                if st.form_submit_button("Add", width='stretch'):
                    final = name.strip() if pick == "— new supplement —" else pick
                    if final:
                        match = next((s for s in catalog.values()
                                      if s["name"].lower() == final.lower()), None)
                        if match:
                            sid = match["id"]
                            if brand.strip() or form:
                                db.ref_upsert("supplements",
                                              {**match,
                                               "brand": brand.strip() or match.get("brand"),
                                               "form": form or match.get("form")})
                        else:
                            sid = db.ref_upsert("supplements", {
                                "name": final, "brand": brand.strip() or None,
                                "form": form or None, "sort_order": len(catalog)})
                        db.ref_upsert("supplement_regimens", {
                            "supplement_id": sid, "dose": dose or None,
                            "unit": unit.strip() or None, "frequency": freq,
                            "start_date": start.isoformat(), "end_date": None,
                            "reason": reason.strip() or None, "status": status,
                            "notes": notes.strip() or None})
                        bust_refs()
                        st.rerun()

    # -- meal times ---------------------------------------------------------
    with tabs[1]:
        st.caption("Prefilled when you pick that occasion on the food panel. "
                   "Snacks always default to the current time.")
        for slot in ("breakfast", "lunch", "dinner"):
            picked = st.time_input(slot.title(), value=slot_default_time(slot),
                                   step=300, key=f"mt_{slot}")
            stored = slot_default_time(slot)
            if picked != stored:
                db.setting_set(C.MEAL_TIME_SETTING.format(slot=slot),
                               picked.strftime("%H:%M"))
                st.rerun()
        st.markdown("<div class='ref'>snack — current time</div>",
                    unsafe_allow_html=True)


    # -- symptoms -----------------------------------------------------------
    with tabs[2]:
        for s in ref_rows("symptom_types"):
            c1, c2 = st.columns([4, 0.8])
            c1.write(s["name"])
            if c2.button("✕", key=f"dy_{s['id']}", width='stretch'):
                db.ref_delete("symptom_types", s["id"])
                bust_refs()
                st.rerun()
        with st.form("add_sym", clear_on_submit=True):
            name = st.text_input("Symptom")
            if st.form_submit_button("Add symptom", width='stretch') and name.strip():
                db.ref_upsert("symptom_types", {
                    "name": name.strip(), "active": True,
                    "sort_order": len(ref_rows("symptom_types"))})
                bust_refs()
                st.rerun()

    # -- habits -------------------------------------------------------------
    with tabs[3]:
        for h in ref_rows("habits"):
            c1, c2 = st.columns([4, 0.8])
            unit = f" ({h['unit']})" if h.get("unit") else ""
            c1.write(f"{h['name']} — {h['kind']}{unit}")
            if c2.button("✕", key=f"dh_{h['id']}", width='stretch'):
                db.ref_delete("habits", h["id"])
                bust_refs()
                st.rerun()
        with st.form("add_habit", clear_on_submit=True):
            name = st.text_input("Habit")
            c1, c2 = st.columns(2)
            kind = c1.selectbox("Type", ["binary", "count"])
            unit = c2.text_input("Unit", placeholder="min, glasses")
            if st.form_submit_button("Add habit", width='stretch') and name.strip():
                db.ref_upsert("habits", {
                    "name": name.strip(), "kind": kind,
                    "unit": unit.strip() or None, "active": True,
                    "sort_order": len(ref_rows("habits"))})
                bust_refs()
                st.rerun()

    # -- export -------------------------------------------------------------
    with tabs[4]:
        rows = db.events_fetch(limit=100000)
        st.write(f"{len(rows)} events logged.")
        if rows:
            df = pd.DataFrame(rows)
            if "detail" in df:
                df["detail"] = df["detail"].apply(
                    lambda v: "" if not v else ";".join(f"{k}={x}" for k, x in v.items()))
            st.download_button("Download all events (CSV)",
                               df.to_csv(index=False).encode(),
                               file_name=f"tracker_events_{date.today()}.csv",
                               mime="text/csv", width='stretch')
            st.dataframe(df.head(40), width='stretch', hide_index=True)
        st.divider()
        if st.button("Seed starter symptom & habit lists", width='stretch'):
            _seed_defaults()
            flash("Seeded. Edit them in the tabs above.")
            st.rerun()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

ROUTES = {
    None: home,
    "food": panel_food,
    "alcohol": panel_alcohol,
    "symptom": panel_symptoms,
    "supplement": panel_supplements,
    "habit": panel_habits,
    "settings": panel_settings,
}

ROUTES[st.session_state.panel]()
