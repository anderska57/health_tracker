"""Health tracker -- beta.

Design rules this app is built around:
  * Logging is retrospective. Every panel opens on a date you can change.
  * Time of day is always prefilled from the clock and always editable.
  * Nothing is typed that can be tapped.
  * Little scrolling: the home screen is a grid of tiles, and each panel keeps
    its Save button above a list of one-line rows.
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

# Colours come from .streamlit/config.toml; this only handles density and shape.
st.markdown("""
<style>
  .block-container {padding: 1.6rem 0.9rem 3rem; max-width: 560px;}
  header[data-testid="stHeader"] {display: none;}   /* it sat over the back arrow */
  [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none;}
  [data-testid="stVerticalBlock"] {gap: 0.55rem;}

  /* Keep columns side by side on a phone instead of stacking them. */
  [data-testid="stHorizontalBlock"] {flex-wrap: nowrap !important; gap: 0.45rem;}
  [data-testid="stColumn"] {min-width: 0 !important;}

  div[data-testid="stButton"] button {min-height: 2.5rem; padding: 0.25rem 0.6rem;}
  div[data-testid="stButton"] button p {font-size: 0.95rem; margin: 0;}

  /* top line: small back arrow + page title */
  .st-key-topbar button {min-height: 2rem !important; border: none;
      background: transparent; padding: 0 0.5rem 0 0;}
  .st-key-topbar button p {font-size: 1.45rem !important; line-height: 1;}
  .ptitle {font-size: 1.3rem; font-weight: 600; margin: 0; line-height: 2rem;}

  /* date line: ◀  Sat, Oct 10  ▶ */
  .st-key-datebar button {min-height: 2.3rem;}

  /* home tiles */
  .st-key-tiles [data-testid="stHorizontalBlock"] {gap: 0.6rem;}
  .st-key-tiles button {height: 6.4rem; flex-direction: column;
      background: var(--tile-bg, rgba(255,255,255,0.04)); border-radius: 1rem;
      border-top-width: 3px;}
  .st-key-tiles button p {white-space: normal !important; overflow: visible !important;
      text-overflow: clip !important; line-height: 1.3; font-size: 0.86rem !important;
      opacity: 0.8;}
  .st-key-tiles button p strong {font-size: 0.98rem; opacity: 1;}
  .st-key-tiles button p:first-line {font-size: 1.55rem;}
  .st-key-tile_food button {border-top-color: #E0A458;}
  .st-key-tile_alcohol button {border-top-color: #C0708A;}
  .st-key-tile_symptom button {border-top-color: #D98466;}
  .st-key-tile_supplement button {border-top-color: #8CBF7A;}
  .st-key-tile_medication button {border-top-color: #7FA7D9;}
  .st-key-tile_habit button {border-top-color: #B39DDB;}

  /* list rows: one line per item */
  [class*="st-key-list"] [data-testid="stVerticalBlock"] {gap: 0.1rem;}
  [class*="st-key-list"] [data-testid="stHorizontalBlock"] {align-items: center;
      padding: 0.15rem 0; border-bottom: 1px solid rgba(127,127,127,0.18);}
  [class*="st-key-list"] [data-testid="stCheckbox"] label p {font-size: 0.98rem;}
  [class*="st-key-list"] input {padding-top: 0.2rem; padding-bottom: 0.2rem; text-align: center;}
  [class*="st-key-list"] [data-baseweb="input"], [class*="st-key-list"] [data-baseweb="select"] > div
      {min-height: 0 !important; height: 2.2rem;}
  [class*="st-key-list"] button {min-height: 2.1rem !important;}

  .sect {font-size: 0.72rem; letter-spacing: 0.07em; text-transform: uppercase;
      opacity: 0.6; margin: 0.6rem 0 0.1rem;}
  .unit {font-size: 0.82rem; opacity: 0.65; white-space: nowrap;}
  .ref {opacity: 0.6; font-size: 0.78rem; line-height: 1.5;}
  .recap {opacity: 0.7; font-size: 0.85rem;}
  hr {margin: 0.4rem 0;}
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

# (route, emoji, tile label). Three across, two rows.
PANELS = [
    ("food", "🍽️", "Food & drink"),
    ("alcohol", "🍷", "Alcohol"),
    ("symptom", "🩹", "Symptoms"),
    ("supplement", "🌿", "Supplements"),
    ("medication", "💊", "Medications"),
    ("habit", "✅", "Habits"),
]

# Supplements and medications are the same machinery: a usual list that is
# assumed taken, with day-level deviations stored as events.
REGIMEN_KINDS = {
    "supplement": {"title": "Supplements", "noun": "supplement"},
    "medication": {"title": "Medications", "noun": "medication"},
}

st.session_state.setdefault("panel", None)
st.session_state.setdefault("log_date", date.today())
st.session_state.setdefault("alc_lines", [])
st.session_state.setdefault("sym_lines", [])
st.session_state.setdefault("flash", None)


def go(panel: str | None):
    st.session_state.panel = panel
    st.session_state.pop("_dialog", None)
    st.session_state.alc_lines = []
    st.session_state.sym_lines = []
    st.rerun()


# Dialogs stay open while st.session_state._dialog names them, and closing one
# (✕, Esc, tapping outside) clears it. Keeping that state explicit, rather than
# tying the dialog to a single button press, keeps it open across reruns.
def open_dialog(name: str):
    st.session_state._dialog = name


def close_dialog():
    st.session_state.pop("_dialog", None)


def dialog_is(name: str) -> bool:
    return st.session_state.get("_dialog") == name


def flash(msg: str):
    st.session_state.flash = msg


def show_flash():
    # A toast instead of a banner, so confirmations don't push the page down.
    if st.session_state.flash:
        st.toast(st.session_state.flash, icon="✅")
        st.session_state.flash = None


def ref_rows(table: str):
    """Reference lists, cached per session so panels don't re-query on every tap."""
    key = f"_ref_{table}"
    if key not in st.session_state:
        st.session_state[key] = db.ref_list(table)
    return st.session_state[key]


def bust_refs():
    for t in ("supplements", "supplement_regimens", "symptom_types", "habits"):
        st.session_state.pop(f"_ref_{t}", None)


def kind_of(s: dict) -> str:
    return s.get("kind") or "supplement"


def section(label: str):
    st.markdown(f"<div class='sect'>{label}</div>", unsafe_allow_html=True)


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


def fmt_qty(v) -> str:
    return "" if v is None else f"{float(v):g}"


def day_events(d: date, category: str) -> list[dict]:
    return db.events_fetch(start=d, end=d, category=category)


def replace_day(category: str, d: date, rows: list[dict]):
    """Idempotent save: clear this category+day, then write the new rows."""
    for e in day_events(d, category):
        db.event_delete(e["id"])
    db.events_insert(rows)


def day_label(d: date) -> str:
    stamp = f"{d:%a}, {d:%b} {d.day}"
    if d == date.today():
        return f"Today · {stamp}"
    if d == date.today() - timedelta(days=1):
        return f"Yesterday · {stamp}"
    return stamp + ("" if d.year == date.today().year else f", {d.year}")


def topbar(title: str):
    """Line 1: a small back arrow at top left with the title beside it."""
    with st.container(key="topbar", horizontal=True, vertical_alignment="center",
                      gap="small"):
        if st.button("←", key="back"):
            go(None)
        st.markdown(f"<div class='ptitle'>{title}</div>", unsafe_allow_html=True)


def header(title: str) -> date:
    """Back + title, then ◀ date ▶ on one line. Tapping the date opens a calendar."""
    topbar(title)
    d = st.session_state.log_date
    with st.container(key="datebar"):
        c1, c2, c3 = st.columns([1, 4.2, 1], vertical_alignment="center")
        if c1.button("◀", key="prev_day", width='stretch', help="Previous day"):
            st.session_state.log_date = d - timedelta(days=1)
            st.rerun()
        with c2.popover(day_label(d), width='stretch'):
            picked = st.date_input("Jump to a date", value=d, max_value=date.today(),
                                   format="MM/DD/YYYY", key=f"jump_{d}")
            if picked != d:
                st.session_state.log_date = picked
                st.rerun()
        nxt = d + timedelta(days=1)
        if c3.button("▶", key="next_day", width='stretch', disabled=nxt > date.today(),
                     help="Next day"):
            st.session_state.log_date = nxt
            st.rerun()
    show_flash()
    return st.session_state.log_date


def action_bar():
    """A slot rendered above a list but filled after it, so the Save button can
    sit at the top while still knowing what changed in the rows below."""
    return st.container(key="actionbar")


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def _tile_status(cat: str, rows: list[dict]) -> str:
    if cat in ("supplement", "medication"):
        return "edited" if rows else "as usual"
    if not rows:
        return "—"
    if cat == "alcohol":
        n = sum(int(r.get("quantity") or 0) for r in rows)
        return f"{n} drink{'s' if n != 1 else ''}"
    if cat == "symptom":
        n = len({(r.get("detail") or {}).get("entry_id") or r["id"] for r in rows})
        return f"{n} logged"
    if cat == "habit":
        n = sum(1 for r in rows if r.get("quantity"))
        return f"{n} done"
    return f"{len(rows)} logged"


def home():
    today = date.today()
    with st.container(horizontal=True, vertical_alignment="center", key="homebar"):
        st.markdown(f"<div class='ptitle'>{today:%A}, {today:%b} {today.day}</div>",
                    unsafe_allow_html=True)
        st.space("stretch")
        if st.button("⚙️", key="settings", help="Settings & export"):
            go("settings")
    show_flash()

    by_cat: dict[str, list[dict]] = {}
    for e in db.events_fetch(start=today, end=today):   # one query, grouped here
        by_cat.setdefault(e["category"], []).append(e)

    with st.container(key="tiles"):
        for i in range(0, len(PANELS), 3):
            cols = st.columns(3)
            for col, (cat, icon, label) in zip(cols, PANELS[i:i + 3]):
                status = _tile_status(cat, by_cat.get(cat, []))
                with col.container(key=f"tile_{cat}"):
                    if st.button(f"{icon}  \n**{label}**  \n{status}", key=f"panel_{cat}",
                                 width='stretch'):
                        st.session_state.log_date = date.today()
                        go(cat)

    st.write("")
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"storage: {db.kind}")
        st.space("stretch")
        if _secret("APP_PASSCODE") and st.button("🔒 Lock this device", key="lock_device",
                                                  type="tertiary"):
            lock_device()


# ---------------------------------------------------------------------------
# Alcohol
# ---------------------------------------------------------------------------

def panel_alcohol():
    d = header("Alcohol")

    for i in range(0, len(C.DRINK_TYPES), 3):
        cols = st.columns(3)
        for col, t in zip(cols, C.DRINK_TYPES[i:i + 3]):
            if col.button(t, key=f"drink_{t}", width='stretch'):
                st.session_state.alc_lines.append(
                    {"uid": str(uuid.uuid4())[:8], "type": t,
                     "qty": 1, "bucket": C.current_bucket()})
                st.rerun()

    lines = st.session_state.alc_lines
    bar = action_bar()
    if lines:
        with st.container(key="list"):
            for line in list(lines):
                c1, c2, c3, c4 = st.columns([1.7, 1.1, 2.1, 0.7], vertical_alignment="center")
                c1.markdown(f"**{line['type']}**")
                line["qty"] = c2.number_input(
                    "drinks", min_value=1, max_value=30, step=1,
                    value=int(line["qty"]), key=f"q_{line['uid']}",
                    label_visibility="collapsed")
                line["bucket"] = c3.selectbox(
                    "time of day", C.TIME_BUCKETS, key=f"b_{line['uid']}",
                    index=C.TIME_BUCKETS.index(line["bucket"]),
                    label_visibility="collapsed")
                if c4.button("✕", key=f"x_{line['uid']}", width='stretch'):
                    lines.remove(line)
                    st.rerun()
        total = sum(int(l["qty"]) for l in lines)
        with bar:
            if st.button(f"Save {total} drink{'s' if total != 1 else ''}",
                         type="primary", width='stretch'):
                db.events_insert([
                    {"event_date": d, "time_bucket": l["bucket"], "category": "alcohol",
                     "item": l["type"], "quantity": int(l["qty"]), "unit": "drinks"}
                    for l in lines])
                st.session_state.alc_lines = []
                flash(f"Logged {total} drink{'s' if total != 1 else ''}.")
                st.rerun()
    else:
        st.caption("Tap a drink to add it. Tap it again for a second time of day.")

    existing = day_events(d, "alcohol")
    if existing:
        section("Logged this day")
        with st.container(key="list_logged"):
            for e in existing:
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
                c1.write(f"{int(e['quantity'])} × {e['item']} · {e['time_bucket']}")
                if c2.button("✕", key=f"del_{e['id']}", width='stretch'):
                    db.event_delete(e["id"])
                    st.rerun()

    st.markdown(
        "<div class='ref' style='margin-top:.8rem'><b>1 standard drink =</b> " +
        " · ".join(f"{k} {v}" for k, v in C.STANDARD_DRINK_REFERENCE.items()) +
        "<br>Log a bigger pour as more drinks — a 10 oz glass of wine is 2.</div>",
        unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Food
# ---------------------------------------------------------------------------

def panel_food():
    d = header("Food & drink")

    rev = st.session_state.setdefault("food_rev", 0)
    slot = st.session_state.setdefault("food_slot_sel", "snack")

    cols = st.columns(4)
    for i, s in enumerate(C.MEAL_SLOTS):
        if cols[i].button(s, key=f"slot_{i}", width='stretch',
                          type="primary" if s == slot else "secondary"):
            st.session_state.food_slot_sel = s
            st.session_state.food_rev = rev + 1   # re-prefill the time
            st.rerun()
    slot = st.session_state.food_slot_sel

    text = st.text_area("what you ate", key=f"food_text_{rev}", height=80,
                        placeholder="oatmeal with blueberries and peanut butter, black coffee",
                        label_visibility="collapsed")
    c1, c2 = st.columns([1.3, 1], vertical_alignment="center")
    when = c1.time_input("time", value=slot_default_time(slot),
                         key=f"food_time_{rev}", step=300,
                         label_visibility="collapsed")
    if c2.button("Save", type="primary", width='stretch') and text.strip():
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
        section("Logged this day")
        with st.container(key="list"):
            for e in existing:
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
                slot_lbl = (e.get("detail") or {}).get("slot", "")
                t = fmt_time(e.get("event_time"))          # blank on pre-time rows
                head = f"**{slot_lbl}**" + (f" · {t}" if t else "")
                c1.markdown(f"{head} — {e['item']}")
                if c2.button("✕", key=f"del_{e['id']}", width='stretch'):
                    db.event_delete(e["id"])
                    st.rerun()


# ---------------------------------------------------------------------------
# Symptoms
# ---------------------------------------------------------------------------

def _sym_add_line(name: str):
    st.session_state.sym_lines.append(
        {"uid": str(uuid.uuid4())[:8], "name": name, "severity": 3,
         "buckets": [C.current_bucket()], "note": ""})


@st.dialog("New symptom", on_dismiss=close_dialog)
def new_symptom_dialog():
    types = ref_rows("symptom_types")
    name = st.text_input("Symptom name", placeholder="e.g. brain fog")
    # Never disabled: on a phone the typed name only reaches the app when the box
    # loses focus, so a button that waits for it would swallow the first tap.
    if st.button("Add symptom", type="primary", width='stretch') and name.strip():
        name = name.strip()
        known = {t["name"].lower(): t["name"] for t in types}
        if name.lower() not in known:
            db.ref_upsert("symptom_types", {"name": name, "active": True,
                                            "sort_order": len(types)})
            bust_refs()
        _sym_add_line(known.get(name.lower(), name))
        close_dialog()
        st.rerun()


def panel_symptoms():
    d = header("Symptoms")
    types = ref_rows("symptom_types")

    names = [t["name"] for t in types] + ["＋ New"]
    for i in range(0, len(names), 3):
        cols = st.columns(3)
        for col, n in zip(cols, names[i:i + 3]):
            if col.button(n, key=f"sym_{n}", width='stretch'):
                if n == "＋ New":
                    open_dialog("new_symptom")
                else:
                    _sym_add_line(n)
                    st.rerun()

    if dialog_is("new_symptom"):
        new_symptom_dialog()

    lines = st.session_state.sym_lines
    bar = action_bar()
    if not lines:
        st.caption("Tap a symptom, then set how bad and when.")
    else:
        sev_opts = [1, 2, 3, 4, 5]
        with st.container(key="list"):
            for line in list(lines):
                with st.container(border=True):
                    c1, c2, c3 = st.columns([1.8, 1.9, 0.7], vertical_alignment="center")
                    c1.markdown(f"**{line['name']}**")
                    line["severity"] = c2.selectbox(
                        "severity", sev_opts, index=sev_opts.index(line["severity"]),
                        key=f"sv_{line['uid']}", format_func=lambda v: C.SEVERITY_LABELS[v],
                        label_visibility="collapsed")
                    if c3.button("✕", key=f"sx_{line['uid']}", width='stretch'):
                        lines.remove(line)
                        st.rerun()
                    picked = st.pills("time of day", C.TIME_BUCKETS, selection_mode="multi",
                                      default=line["buckets"], key=f"sb_{line['uid']}",
                                      label_visibility="collapsed")
                    line["buckets"] = [b for b in C.TIME_BUCKETS if b in (picked or [])]
                    line["note"] = st.text_input(
                        "note", value=line["note"], key=f"sn_{line['uid']}",
                        placeholder="note (optional)", label_visibility="collapsed")

        missing = [l["name"] for l in lines if not l["buckets"]]
        with bar:
            if missing:
                st.caption("Pick a time of day for: " + ", ".join(missing))
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
        section("Logged this day")
        with st.container(key="list_logged"):
            for gid, rows in groups.items():
                first = rows[0]
                seen = {r["time_bucket"] for r in rows}
                when = ", ".join(b for b in C.TIME_BUCKETS if b in seen)
                note = f" — {first['note']}" if first.get("note") else ""
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
                c1.write(f"{first['item']} · {first['severity']}/5 · {when}{note}")
                if c2.button("✕", key=f"del_{gid}", width='stretch'):
                    for r in rows:
                        db.event_delete(r["id"])
                    st.rerun()


# ---------------------------------------------------------------------------
# Supplements & medications
# ---------------------------------------------------------------------------

# Rows the Save button owns for a day. One-off "extra" rows are written and
# removed individually, so a save never wipes them.
_SUP_SAVE_KINDS = {"skipped", "dose_changed", "taken", "exception", "day_confirmed"}
_NEW = "— new —"


def _add_item(kind: str, d: date, regimens: list[dict], *, pick: str, typed: str,
              dose: float, unit: str, usual: bool, freq: str, brand: str,
              reason: str) -> str | None:
    """Add a one-off for the day, or start a usual regimen. Returns an error
    message, or None on success."""
    noun = REGIMEN_KINDS[kind]["noun"]
    catalog = ref_rows("supplements")
    name = (typed if pick == _NEW else pick).strip()
    if not name:
        return f"Give the {noun} a name."
    match = next((s for s in catalog if s["name"].lower() == name.lower()), None)
    if match and kind_of(match) != kind:
        other = REGIMEN_KINDS[kind_of(match)]["noun"]
        return f"{match['name']} is already on your list as a {other}."
    if usual and match and match["id"] in {r["supplement_id"] for r in regimens}:
        return f"{match['name']} is already in your usual list."
    sid = match["id"] if match else db.ref_upsert("supplements", {
        "name": name, "brand": brand or None, "kind": kind, "sort_order": len(catalog)})
    if usual:
        db.ref_upsert("supplement_regimens", {
            "supplement_id": sid, "dose": dose or None, "unit": unit or None,
            "frequency": freq, "start_date": d.isoformat(), "end_date": None,
            "status": "active", "reason": reason or None, "notes": None})
        flash(f"{name} added to your usual list from {d:%b} {d.day}.")
    else:
        db.events_insert([{
            "event_date": d, "category": kind, "item": name,
            "quantity": dose or None, "unit": unit or None, "note": reason or None,
            "detail": {"kind": "extra", "supplement_id": sid}}])
        flash(f"Logged {name} for this day.")
    bust_refs()
    return None


def _add_dialog_body(kind: str, d: date, regimens: list[dict], usual_default: bool):
    noun = REGIMEN_KINDS[kind]["noun"]
    catalog = [s for s in ref_rows("supplements") if kind_of(s) == kind]
    p = f"add_{kind}"
    pick = st.selectbox(noun.capitalize(), [_NEW] + [s["name"] for s in catalog], key=f"{p}_pick")
    typed = st.text_input("Name", placeholder="Vitamin C" if kind == "supplement" else "",
                          key=f"{p}_name") if pick == _NEW else ""
    c1, c2 = st.columns(2)
    dose = c1.number_input("Dose", min_value=0.0, step=0.5, value=1.0, format="%g",
                           key=f"{p}_dose")
    unit = c2.text_input("Unit", placeholder="capsule, mg", key=f"{p}_unit")
    usual = st.checkbox("Part of my usual routine", value=usual_default, key=f"{p}_usual",
                        help="Usual items are assumed taken every day they're due.")
    freq = st.selectbox("How often", C.SUPPLEMENT_FREQUENCIES, key=f"{p}_freq") if usual else ""
    c1, c2 = st.columns(2)
    brand = c1.text_input("Brand (optional)", key=f"{p}_brand") if pick == _NEW else ""
    reason = c2.text_input("What for (optional)", key=f"{p}_reason")
    if st.button("Add", type="primary", width='stretch', key=f"{p}_go"):
        err = _add_item(kind, d, regimens, pick=pick, typed=typed or "", dose=dose,
                        unit=(unit or "").strip(), usual=usual, freq=freq,
                        brand=(brand or "").strip(), reason=(reason or "").strip())
        if err:
            st.warning(err)
        else:
            close_dialog()
            st.rerun()


_ADD_DIALOGS = {k: st.dialog(f"Add a {v['noun']}", on_dismiss=close_dialog)(_add_dialog_body)
                for k, v in REGIMEN_KINDS.items()}


def panel_regimen(kind: str):
    meta = REGIMEN_KINDS[kind]
    noun = meta["noun"]
    d = header(meta["title"])

    if not getattr(db, "supports_medications", True) and kind == "medication":
        st.info("Medications need one small database update first. In Supabase, open "
                "**SQL Editor**, paste this line and press **Run**, then reload the app:")
        st.code("alter table supplements add column if not exists kind text "
                "not null default 'supplement';", language="sql")
        return

    regimens = db.regimens_on(d, kind)
    bar = action_bar()

    if not regimens:
        with bar:
            if st.button(f"＋ Add {noun}", width='stretch', key="add_btn"):
                open_dialog(f"add_{kind}")
        if dialog_is(f"add_{kind}"):
            _ADD_DIALOGS[kind](kind, d, regimens, True)
        st.markdown(f"**What do you usually take?**  \nAdd each {noun} you take regularly "
                    "and how often. From then on they're assumed taken unless you say "
                    "otherwise.")
        return

    daily = [r for r in regimens if C.is_daily(r.get("frequency"))]
    occasional = [r for r in regimens if not C.is_daily(r.get("frequency"))]

    # What was already recorded for this day, keyed by regimen id (older rows by name).
    existing = day_events(d, kind)
    skipped, dose_seen, taken, extras, stored = set(), {}, set(), [], set()
    for e in existing:
        det = e.get("detail") or {}
        k, key = det.get("kind"), det.get("regimen_id") or e.get("item")
        if k == "skipped" or (k == "exception" and det.get("exception") == "skipped"):
            skipped.add(key)
        elif k == "dose_changed":
            dose_seen[key] = e.get("quantity")
        elif k == "taken":
            taken.add(key)
            if e.get("quantity") is not None:
                dose_seen[key] = e["quantity"]
        elif k == "extra" or (k == "exception" and det.get("exception") == "extra"):
            extras.append(e)
        if k in ("skipped", "dose_changed", "taken"):
            stored.add((k, key, e.get("quantity") if k != "skipped" else None))

    def has(s, r):
        return r["id"] in s or r["name"] in s

    rows, wanted = [], set()

    def item_row(r, default_on: bool):
        """One line: checkbox + name | dose | unit."""
        k = f"{kind}_{d}_{r['id']}"
        base = float(r["dose"]) if r.get("dose") is not None else 1.0
        prior = dose_seen.get(r["id"], dose_seen.get(r["name"]))
        c1, c2, c3 = st.columns([4.6, 1.35, 1.25], vertical_alignment="center")
        on = c1.checkbox(r["name"], value=default_on, key=f"st_{k}")
        dose = c2.number_input(f"{r['name']} dose", min_value=0.0, step=0.5, format="%g",
                               value=float(prior) if prior is not None else base,
                               key=f"sd_{k}", disabled=not on,
                               label_visibility="collapsed")
        c3.markdown(f"<span class='unit'>{r.get('unit') or ''}</span>",
                    unsafe_allow_html=True)
        return on, dose, base

    with st.container(key="list"):
        if daily:
            section("Daily — uncheck anything skipped")
            for r in daily:
                on, dose, base = item_row(r, not has(skipped, r))
                if not on:
                    rows.append({"item": r["name"],
                                 "detail": {"kind": "skipped", "regimen_id": r["id"]}})
                    wanted.add(("skipped", r["id"], None))
                elif abs(dose - base) > 1e-9:
                    rows.append({"item": r["name"], "quantity": dose, "unit": r.get("unit"),
                                 "detail": {"kind": "dose_changed", "regimen_id": r["id"],
                                            "usual_dose": base}})
                    wanted.add(("dose_changed", r["id"], dose))

        if occasional:
            section("Weekly & as needed — check if taken")
            for r in occasional:
                on, dose, _ = item_row(r, has(taken, r))
                if on:
                    rows.append({"item": r["name"], "quantity": dose, "unit": r.get("unit"),
                                 "detail": {"kind": "taken", "regimen_id": r["id"],
                                            "frequency": r.get("frequency")}})
                    wanted.add(("taken", r["id"], dose))

        if extras:
            section("Also taken this day")
            for e in extras:
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
                amt = f" · {fmt_qty(e['quantity'])} {e.get('unit') or ''}".rstrip() \
                    if e.get("quantity") else ""
                c1.write(f"{e['item']}{amt}")
                if c2.button("✕", key=f"del_{e['id']}", width='stretch'):
                    db.event_delete(e["id"])
                    st.rerun()

    # Compare against what's stored by regimen id, so older name-keyed rows
    # simply read as "changed" and get rewritten in the new shape on save.
    changed = wanted != stored
    with bar:
        c1, c2 = st.columns(2)
        save = c1.button("Save changes" if changed else "No changes", key="save_btn",
                         type="primary", width='stretch', disabled=not changed)
        add = c2.button(f"＋ Add {noun}", key="add_btn", width='stretch')
    if save:
        for e in existing:
            if (e.get("detail") or {}).get("kind") in _SUP_SAVE_KINDS:
                db.event_delete(e["id"])
        db.events_insert([{**row, "event_date": d, "category": kind} for row in rows])
        n_skip = sum(1 for r in rows if r["detail"]["kind"] == "skipped")
        n_dose = sum(1 for r in rows if r["detail"]["kind"] == "dose_changed")
        bits = [f"{n_skip} skipped" if n_skip else "",
                f"{n_dose} dose change{'s' if n_dose != 1 else ''}" if n_dose else ""]
        flash("Saved — " + (", ".join(b for b in bits if b)
                            or "your usual list stands for this day") + ".")
        st.rerun()
    if add:
        open_dialog(f"add_{kind}")
    if dialog_is(f"add_{kind}"):
        _ADD_DIALOGS[kind](kind, d, regimens, False)


def panel_supplements():
    panel_regimen("supplement")


def panel_medications():
    panel_regimen("medication")


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

def panel_habits():
    d = header("Habits")
    habits = ref_rows("habits")
    if not habits:
        st.info("No habits defined yet. Add them in Settings (⚙️ on the home screen).")
        return

    existing = {e["item"]: e for e in day_events(d, "habit")}
    bar = action_bar()

    values: dict[str, float] = {}
    with st.container(key="list"):
        for h in habits:
            prior = existing.get(h["name"])
            if h["kind"] == "binary":
                (c1,) = st.columns(1)     # a column row, so it gets the row divider
                done = c1.checkbox(h["name"], value=bool(prior and prior["quantity"]),
                                   key=f"hb_{h['id']}")
                values[h["name"]] = 1 if done else 0
            else:
                c1, c2, c3 = st.columns([4.6, 1.35, 1.25], vertical_alignment="center")
                target = f" <span class='unit'>goal {h['target']:g}</span>" if h.get("target") else ""
                c1.markdown(f"{h['name']}{target}", unsafe_allow_html=True)
                values[h["name"]] = c2.number_input(
                    h["name"], min_value=0.0, step=1.0, format="%g",
                    value=float(prior["quantity"]) if prior else 0.0,
                    key=f"hc_{h['id']}", label_visibility="collapsed")
                c3.markdown(f"<span class='unit'>{h.get('unit') or ''}</span>",
                            unsafe_allow_html=True)

    with bar:
        label = "Update day" if existing else "Save day"
        if st.button(label, type="primary", width='stretch', key="save_btn"):
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


def _settings_regimens(kind: str):
    noun = REGIMEN_KINDS[kind]["noun"]
    if kind == "medication" and not getattr(db, "supports_medications", True):
        st.info("Open the Medications tile for the one-line database update this needs.")
        return
    st.caption("A regimen is a stretch of time you intended to take something a "
               "certain way. Stopping and restarting later makes a new regimen, so "
               "separate trials stay separable.")
    catalog = {s["id"]: s for s in ref_rows("supplements") if kind_of(s) == kind}
    regimens = [r for r in ref_rows("supplement_regimens") if r["supplement_id"] in catalog]

    with st.container(key=f"list_reg_{kind}"):
        for r in regimens:
            s = catalog[r["supplement_id"]]
            dose = f"{r['dose']:g} {r.get('unit') or ''}".strip() if r.get("dose") else ""
            span = (f"{r['start_date']} → {r['end_date']}" if r.get("end_date")
                    else f"since {r['start_date']}")
            meta = " · ".join(x for x in [dose, r.get("frequency"), span, s.get("brand"),
                                          r.get("reason")] if x)
            status = "" if r["status"] == "active" else f" <span class='unit'>{r['status']}</span>"
            c1, c2, c3 = st.columns([4.2, 1.3, 0.7], vertical_alignment="center")
            c1.markdown(f"**{s['name']}**{status}  \n<span class='ref'>{meta}</span>",
                        unsafe_allow_html=True)
            if r["status"] == "active" and c2.button("Stop", key=f"stop_{r['id']}",
                                                     width='stretch'):
                db.ref_upsert("supplement_regimens",
                              {**r, "status": "stopped",
                               "end_date": date.today().isoformat()})
                bust_refs()
                st.rerun()
            if c3.button("✕", key=f"dr_{r['id']}", width='stretch'):
                db.ref_delete("supplement_regimens", r["id"])
                bust_refs()
                st.rerun()

    with st.expander(f"Add a {noun} / regimen"):
        with st.form(f"add_regimen_{kind}", clear_on_submit=True):
            names = [_NEW] + [s["name"] for s in catalog.values()]
            pick = st.selectbox(noun.capitalize(), names)
            name = st.text_input("New name")
            c1, c2 = st.columns(2)
            brand = c1.text_input("Brand")
            form = c2.selectbox("Form", [""] + C.SUPPLEMENT_FORMS)
            c1, c2, c3 = st.columns(3)
            dose = c1.number_input("Dose", min_value=0.0, step=0.5, value=1.0)
            unit = c2.text_input("Unit", placeholder="capsule, mg")
            freq = c3.selectbox("Frequency", C.SUPPLEMENT_FREQUENCIES)
            c1, c2 = st.columns(2)
            start = c1.date_input("Start date", value=date.today())
            status = c2.selectbox("Status", C.REGIMEN_STATUSES)
            reason = st.text_input("What for")
            notes = st.text_input("Notes")

            if st.form_submit_button("Add", width='stretch'):
                final = name.strip() if pick == _NEW else pick
                if final:
                    everything = ref_rows("supplements")
                    match = next((s for s in everything
                                  if s["name"].lower() == final.lower()), None)
                    if match and kind_of(match) != kind:
                        st.warning(f"{match['name']} is already listed under "
                                   f"{REGIMEN_KINDS[kind_of(match)]['title']}.")
                        return
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
                            "form": form or None, "kind": kind,
                            "sort_order": len(everything)})
                    db.ref_upsert("supplement_regimens", {
                        "supplement_id": sid, "dose": dose or None,
                        "unit": unit.strip() or None, "frequency": freq,
                        "start_date": start.isoformat(), "end_date": None,
                        "reason": reason.strip() or None, "status": status,
                        "notes": notes.strip() or None})
                    bust_refs()
                    st.rerun()


def panel_settings():
    topbar("Settings")
    show_flash()

    tabs = st.tabs(["Supplements", "Medications", "Meal times", "Symptoms",
                    "Habits", "Export"])

    with tabs[0]:
        _settings_regimens("supplement")
    with tabs[1]:
        _settings_regimens("medication")

    # -- meal times ---------------------------------------------------------
    with tabs[2]:
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
    with tabs[3]:
        with st.container(key="list_sym"):
            for s in ref_rows("symptom_types"):
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
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
    with tabs[4]:
        with st.container(key="list_hab"):
            for h in ref_rows("habits"):
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
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
    with tabs[5]:
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
    "medication": panel_medications,
    "habit": panel_habits,
    "settings": panel_settings,
}

ROUTES.get(st.session_state.panel, home)()
