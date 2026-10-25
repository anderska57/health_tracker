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
import json
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
  .st-key-device_read, .st-key-device_write {position: absolute; height: 0; overflow: hidden;}
  [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none;}
  [data-testid="stVerticalBlock"] {gap: 0.55rem;}

  /* Keep columns side by side on a phone instead of stacking them. */
  [data-testid="stHorizontalBlock"] {flex-wrap: nowrap !important; gap: 0.45rem;}
  [data-testid="stColumn"] {min-width: 0 !important;}

  div[data-testid="stButton"] button {min-height: 2.5rem; padding: 0.25rem 0.6rem;}
  div[data-testid="stButton"] button p {font-size: 0.95rem; margin: 0;}
  /* A tapped button kept a grey "focused" look on phones, which read as
     selected. Selection is shown deliberately (filled teal) instead. */
  div[data-testid="stButton"] button[kind="secondary"],
  [data-testid="stPopover"] button {background-color: __BTN_BG__;}
  @media (hover: none) {
    div[data-testid="stButton"] button[kind="secondary"]:hover,
    div[data-testid="stButton"] button[kind="secondary"]:focus {
        background-color: __BTN_BG__; border-color: __BORDER__; color: inherit;}
    [data-testid="stButtonGroup"] button[kind="pills"]:hover,
    [data-testid="stButtonGroup"] button[kind="pills"]:focus {
        background-color: __PAGE_BG__; border-color: __BORDER__; color: inherit;}
  }

  /* top line: boxed back button | centred title | spacer of the same width */
  .st-key-topbar [data-testid="stHorizontalBlock"] {align-items: center;}
  .st-key-back button {width: 3rem; height: 2.75rem; min-height: 2.75rem !important;
      padding: 0 !important;}
  .st-key-back button p {font-size: 1.35rem !important; line-height: 1;}
  .ptitle {font-size: 1.3rem; font-weight: 650; margin: 0; line-height: 2.75rem;
      text-align: center; white-space: nowrap;}

  /* date line: ◀  Sat, Oct 10  ▶ */
  .st-key-datebar button {min-height: 2.3rem;}

  /* home tiles */
  .st-key-tiles [data-testid="stHorizontalBlock"] {gap: 0.6rem;}
  .st-key-tiles button {height: 6.4rem; flex-direction: column; border-radius: 1rem;
      border-top-width: 4px; box-shadow: 0 1px 2px rgba(29,59,56,0.08);}
  .st-key-tiles button p {white-space: normal !important; overflow: visible !important;
      text-overflow: clip !important; line-height: 1.3; font-size: 0.86rem !important;}
  .st-key-tiles button p strong {font-size: 0.93rem; font-weight: 700; color: __TEXT__;
      white-space: nowrap; letter-spacing: -0.01em;}
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
  /* a row with a second line (medication "why?") divides as one unit */
  [class*="st-key-rowgroup_"] [data-testid="stHorizontalBlock"] {border-bottom: none;}
  [class*="st-key-rowgroup_"] {border-bottom: 1px solid rgba(127,127,127,0.18);
      padding-bottom: 0.3rem;}
  [class*="st-key-rowgroup_"] [data-testid="stButtonGroup"] {padding-left: 2rem;}

  /* symptom picker: scrolls once the list grows */
  .st-key-sympick [data-testid="stButtonGroup"] {gap: 0.35rem;}

  .loghead {font-size: 0.78rem; letter-spacing: 0.06em; text-transform: uppercase;
      font-weight: 700; margin-top: 0.5rem;}
  .delhead {font-size: 0.78rem; text-align: right; opacity: 0.7; margin-top: 0.5rem;}
  [class*="st-key-loghead"] [data-testid="stHorizontalBlock"] {border-bottom: none !important;
      padding-bottom: 0 !important;}
  .sect {font-size: 0.72rem; letter-spacing: 0.07em; text-transform: uppercase;
      opacity: 0.6; margin: 0.6rem 0 0.1rem;}
  .unit {font-size: 0.82rem; opacity: 0.65; white-space: nowrap;}
  .ref {opacity: 0.6; font-size: 0.78rem; line-height: 1.5;}
  .recap {opacity: 0.7; font-size: 0.85rem;}
  .steplabel {opacity: 0.75; font-size: 0.85rem; margin: 0.1rem 0 0.35rem;}
  /* completeness ranges: five across on a phone */
  [class*="st-key-list_comp"] [data-testid="stButtonGroup"] {gap: 0.3rem;}
  [class*="st-key-list_comp"] [data-testid="stButtonGroup"] button {padding: 0.2rem 0.55rem;
      min-height: 2.1rem;}
  hr {margin: 0.4rem 0;}
</style>
""".replace("__BTN_BG__", st.get_option("theme.secondaryBackgroundColor") or "transparent")
   .replace("__TEXT__", st.get_option("theme.textColor") or "inherit")
   .replace("__PAGE_BG__", st.get_option("theme.backgroundColor") or "transparent")
   .replace("__BORDER__", st.get_option("theme.borderColor") or "rgba(127,127,127,0.3)"),
   unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Passcode gate
# ---------------------------------------------------------------------------
# Active only when APP_PASSCODE is set in secrets, so local runs stay open.
# Three ways in: a remembered device, the passcode in the URL (?k=...), or
# typing it. The Supabase key never leaves the server either way: Streamlit
# runs this Python server-side and sends only rendered UI to the browser.
#
# "Remember this device" keeps a key in the browser's localStorage and a tiny
# component hands it to Python. (An earlier version used a cookie, but
# Streamlit Community Cloud's proxy strips cookies before they reach the app,
# and Safari also caps script-set cookies at 7 days.) iPhone home-screen web
# apps keep their own localStorage for as long as you keep using them. The key
# is an HMAC of the passcode, never the passcode itself, so changing
# APP_PASSCODE locks out every remembered device.

_DEVICE_JS = """
export default function(component) {
  const { data, setStateValue } = component;
  const KEY = "tracker_device";
  let value = "";
  try {
    if (data && typeof data.write === "string") {
      if (data.write) { localStorage.setItem(KEY, data.write); }
      else { localStorage.removeItem(KEY); }
    }
    value = localStorage.getItem(KEY) || "";
  } catch (e) { value = ""; }
  setStateValue("token", value);
}
"""
_device_store = st.components.v2.component("device_store", js=_DEVICE_JS)


def _passcode_ok(supplied: str | None, secret: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied.strip().encode(), secret.encode())


def _device_token(secret: str) -> str:
    return hmac.new(secret.encode(), b"tracker:remember-this-device", "sha256").hexdigest()


def _device_component(key: str, write: str | None = None) -> str | None:
    """Mount the localStorage helper. Returns the stored key once the browser
    has reported it ("" if none), or None before it has."""
    data = {} if write is None else {"write": write}
    try:
        res = _device_store(key=key, data=data, on_token_change=lambda: None)
        return getattr(res, "token", None)
    except Exception:          # no browser (headless tests) or component error
        return None


def require_passcode():
    secret = _secret("APP_PASSCODE")
    if not secret:
        return
    ss = st.session_state

    if ss.get("_authed"):
        if "_remember_pending" in ss:
            # Saved at the END of the page (see flush_device_store): putting an
            # element above everything else shifted the page and left ghost
            # copies of home-screen buttons on later screens.
            ss._device_write = _device_token(secret) if ss.pop("_remember_pending") else ""
        return

    if _passcode_ok(st.query_params.get("k"), secret):
        ss._authed = True
        return

    forget = ss.pop("_forget_pending", False)
    stored = _device_component("device_read", "" if forget else None)
    if (stored and not forget and not ss.get("_ignore_device")
            and hmac.compare_digest(stored.encode(), _device_token(secret).encode())):
        ss._authed = True
        return

    st.markdown("## 🔒 Tracker")
    if stored is None and not ss.get("_ignore_device"):
        st.caption("Checking for a remembered device…")
    with st.form("passcode"):
        pw = st.text_input("Passcode", type="password")
        remember = st.checkbox("Remember this device", value=True)
        if st.form_submit_button("Unlock", type="primary", width='stretch'):
            if _passcode_ok(pw, secret):
                ss._authed = True
                ss._remember_pending = remember
                ss.pop("_ignore_device", None)
                st.rerun()
            st.error("That passcode didn't match.")
    st.stop()


def flush_device_store():
    """Called once the page is drawn, so the helper sits below everything else."""
    write = st.session_state.pop("_device_write", None)
    if write is not None:
        _device_component("device_write", write)


def lock_device():
    """Forget this device and return to the lock screen."""
    ss = st.session_state
    ss._authed = False
    ss._forget_pending = True
    ss._ignore_device = True      # don't let this session's last reading log you back in
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
st.session_state.setdefault("medsym_lines", [])
st.session_state.setdefault("flash", None)


def go(panel: str | None):
    st.session_state.panel = panel
    if panel in (None, "review"):
        st.session_state.pop("_back_to", None)
    st.session_state.pop("_dialog", None)
    st.session_state.alc_lines = []
    st.session_state.sym_lines = []
    st.session_state.medsym_lines = []
    st.session_state.sym_rev = st.session_state.get("sym_rev", 0) + 1
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


def logged_header(title: str = "Logged this day", key: str = "loghead"):
    """Bold section title, with 'delete entry' right-aligned over the ✕ buttons."""
    with st.container(key=key):
        c1, c2 = st.columns([3.4, 2.3], vertical_alignment="bottom")
        c1.markdown(f"<div class='loghead'>{title}</div>", unsafe_allow_html=True)
        c2.markdown("<div class='delhead'>delete entry</div>", unsafe_allow_html=True)


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


# 12-hour clock with am/pm where this Streamlit version supports it (Cloud
# installs the newest; older local installs fall back to 24-hour).
import inspect as _inspect
TIME_FMT = ({"format": "12h"} if "format" in _inspect.signature(st.time_input).parameters
            else {})


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
    """Line 1: a boxed back button, the title centred, and an empty column the
    same width as the button on the right so the title is truly centred."""
    with st.container(key="topbar"):
        c1, c2, c3 = st.columns([1, 4.2, 1], vertical_alignment="center")
        if c1.button("←", key="back"):
            # Panels opened from the weekly review return to it.
            go(st.session_state.get("_back_to"))
        c2.markdown(f"<div class='ptitle'>{title}</div>", unsafe_allow_html=True)


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

def habits_done(d: date, rows: list[dict]) -> int:
    """Habits done on a day, counting usual habits that weren't unchecked."""
    explicit = {r["item"]: r for r in rows}
    plans = habit_plans()
    return sum(1 for h in ref_rows("habits")
               if (habit_value(h, d, explicit, plans)[0] or 0) > 0)


def _tile_status(cat: str, rows: list[dict]) -> str:
    if cat in ("supplement", "medication"):
        return "edited" if rows else "as usual"
    if cat == "habit" and ref_rows("habits"):
        return f"{habits_done(date.today(), rows)} done"
    if not rows:
        return "—"
    if cat == "alcohol":
        n = sum(int(r.get("quantity") or 0) for r in rows)
        return f"{n} drink{'s' if n != 1 else ''}"
    if cat == "symptom":
        n = len({(r.get("detail") or {}).get("entry_id") or r["id"] for r in rows})
        return f"{n} logged"
    return f"{len(rows)} logged"


def open_review(ws: date):
    st.session_state.review_week = ws
    st.session_state.review_step = 0
    go("review")


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
                    if st.button(f"{icon}  \n**{label}**  \n:gray[{status}]", key=f"panel_{cat}",
                                 width='stretch'):
                        st.session_state.log_date = date.today()
                        go(cat)

    target = review_target(today)
    due = target.isoformat() not in reviewed_weeks()
    if due and st.button(f"📋  Review your week · {week_label(target)}", key="review_card",
                         type="primary", width='stretch'):
        open_review(target)

    st.write("")
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"storage: {db.kind}")
        st.space("stretch")
        if not due and st.button("📋 Weekly review", key="review_link", type="tertiary"):
            open_review(target)
        if _secret("APP_PASSCODE") and st.button("🔒 Lock this device", key="lock_device",
                                                  type="tertiary"):
            lock_device()


# ---------------------------------------------------------------------------
# Alcohol
# ---------------------------------------------------------------------------

def panel_alcohol():
    d = header("Alcohol")

    chosen = {l["type"] for l in st.session_state.alc_lines}
    for i in range(0, len(C.DRINK_TYPES), 3):
        cols = st.columns(3)
        for col, t in zip(cols, C.DRINK_TYPES[i:i + 3]):
            if col.button(t, key=f"drink_{t}", width='stretch',
                          type="primary" if t in chosen else "secondary"):
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
        logged_header()
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
    when = c1.time_input("time", value=slot_default_time(slot), **TIME_FMT,
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
        logged_header()
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
# The picker, the per-symptom editor and the save logic are shared by the
# Symptoms page and the "log the symptom" pop-up that follows a medication
# taken for a symptom, so both ask exactly the same questions.

SYM_PICK_SCROLL_AFTER = 12     # symptoms; beyond this the picker scrolls


def _sym_new_line(name: str) -> dict:
    return {"uid": str(uuid.uuid4())[:8], "name": name, "severity": 3,
            "buckets": [C.current_bucket()], "note": ""}


def symptom_names() -> list[str]:
    return sorted((t["name"] for t in ref_rows("symptom_types")), key=str.lower)


def ensure_symptom_type(name: str) -> str:
    """Return the stored spelling of a symptom, creating it if it's new."""
    types = ref_rows("symptom_types")
    known = {t["name"].lower(): t["name"] for t in types}
    if name.lower() in known:
        return known[name.lower()]
    db.ref_upsert("symptom_types", {"name": name, "active": True, "sort_order": len(types)})
    bust_refs()
    return name


def _bump(rev_key: str):
    st.session_state[rev_key] = st.session_state.get(rev_key, 0) + 1


def symptom_picker(lines_key: str, scope: str):
    """Alphabetical chips; tapping one adds it to the list below (highlighted),
    tapping again removes it. Scrolls once the list grows."""
    ss = st.session_state
    lines = ss[lines_key]
    names = symptom_names()
    rev = ss.setdefault(f"{scope}_rev", 0)
    chosen = [l["name"] for l in lines if l["name"] in names]
    scrolls = len(names) > SYM_PICK_SCROLL_AFTER
    with st.container(key=f"{scope}pick", height=168 if scrolls else "content",
                      border=scrolls):
        picked = st.pills("symptoms", names, selection_mode="multi", default=chosen,
                          key=f"{scope}_pick_{rev}", label_visibility="collapsed") or []
    have = {l["name"] for l in lines}
    for n in picked:
        if n not in have:
            lines.append(_sym_new_line(n))
    lines[:] = [l for l in lines if l["name"] in picked or l["name"] not in names]


def symptom_editor(line: dict, lines_key: str, scope: str):
    """One symptom: name · severity · ✕, then times of day, then a note."""
    sev_opts = [1, 2, 3, 4, 5]
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.5, 2.3, 0.65], vertical_alignment="center")
        c1.markdown(f"**{line['name']}**")
        line["severity"] = c2.selectbox(
            "severity", sev_opts, index=sev_opts.index(line["severity"]),
            key=f"{scope}_sv_{line['uid']}", format_func=lambda v: C.SEVERITY_LABELS[v],
            label_visibility="collapsed")
        if c3.button("✕", key=f"{scope}_sx_{line['uid']}", width='stretch'):
            st.session_state[lines_key].remove(line)
            _bump(f"{scope}_rev")
            st.rerun()
        picked = st.pills("time of day", C.TIME_BUCKETS, selection_mode="multi",
                          default=line["buckets"], key=f"{scope}_sb_{line['uid']}",
                          label_visibility="collapsed")
        line["buckets"] = [b for b in C.TIME_BUCKETS if b in (picked or [])]
        line["note"] = st.text_input(
            "note", value=line["note"], key=f"{scope}_sn_{line['uid']}",
            placeholder="note (optional)", label_visibility="collapsed")


def symptom_rows(lines: list[dict], d: date, extra: dict | None = None) -> list[dict]:
    # One row per time of day keeps time_bucket queryable alongside alcohol
    # and food; entry_id ties the rows back into one entry.
    rows = []
    for l in lines:
        entry_id = str(uuid.uuid4())
        for b in l["buckets"]:
            rows.append({
                "event_date": d, "time_bucket": b, "category": "symptom",
                "item": l["name"], "severity": int(l["severity"]),
                "note": l["note"].strip() or None,
                "detail": {"entry_id": entry_id, "buckets": l["buckets"], **(extra or {})},
            })
    return rows


@st.dialog("New symptom", on_dismiss=close_dialog)
def new_symptom_dialog():
    name = st.text_input("Symptom name", placeholder="e.g. brain fog")
    # Never disabled: on a phone the typed name only reaches the app when the box
    # loses focus, so a button that waits for it would swallow the first tap.
    if st.button("Add symptom", type="primary", width='stretch') and name.strip():
        name = ensure_symptom_type(name.strip())
        if name not in {l["name"] for l in st.session_state.sym_lines}:
            st.session_state.sym_lines.append(_sym_new_line(name))
        _bump("sym_rev")
        close_dialog()
        st.rerun()


def panel_symptoms():
    d = header("Symptoms")
    ss = st.session_state
    lines = ss.sym_lines
    bar = action_bar()

    if dialog_is("new_symptom"):
        new_symptom_dialog()

    symptom_picker("sym_lines", "sym")
    if not lines:
        st.caption("Tap the symptoms you had, then set how bad and when.")
    else:
        with st.container(key="list"):
            for line in list(lines):
                symptom_editor(line, "sym_lines", "sym")

    missing = [l["name"] for l in lines if not l["buckets"]]
    with bar:
        c1, c2 = st.columns(2)
        n = len(lines)
        save = c1.button(f"Save {n} symptom{'s' if n != 1 else ''}" if n else "Save",
                         type="primary", width='stretch', key="save_btn",
                         disabled=not n or bool(missing))
        if c2.button("＋ New symptom", width='stretch', key="add_btn"):
            open_dialog("new_symptom")
            st.rerun()
        if missing:
            st.caption("Pick a time of day for: " + ", ".join(missing))
    if save:
        db.events_insert(symptom_rows(lines, d))
        ss.sym_lines = []
        _bump("sym_rev")
        flash("Symptoms logged.")
        st.rerun()

    existing = day_events(d, "symptom")
    if existing:
        groups: dict[str, list[dict]] = {}
        for e in existing:
            gid = (e.get("detail") or {}).get("entry_id") or e["id"]
            groups.setdefault(gid, []).append(e)
        logged_header()
        with st.container(key="list_logged"):
            for gid, rows in groups.items():
                first = rows[0]
                seen = {r["time_bucket"] for r in rows}
                when = ", ".join(b for b in C.TIME_BUCKETS if b in seen)
                note = f" — {first['note']}" if first.get("note") else ""
                treated = (first.get("detail") or {}).get("treated_with")
                took = f" · took {', '.join(treated)}" if treated else ""
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
                c1.write(f"{first['item']} · {first['severity']}/5 · {when}{took}{note}")
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

# Why a medication was taken. Asked for as-needed and one-off medications;
# a daily medication is routine, so it isn't asked every day.
PURPOSE_LABELS = {"preventive": "preventive", "symptom": "for a symptom"}
_PURPOSE_BY_LABEL = {v: k for k, v in PURPOSE_LABELS.items()}


def purpose_picker(key: str, current: str | None) -> str | None:
    label = st.pills("why", list(PURPOSE_LABELS.values()), selection_mode="single",
                     default=PURPOSE_LABELS.get(current), key=key,
                     label_visibility="collapsed")
    return _PURPOSE_BY_LABEL.get(label)


def start_symptom_followup(d: date, meds: list[str]):
    """After a medication taken for a symptom: offer to log that symptom."""
    st.session_state._medsym = {"date": d.isoformat(), "meds": meds}
    st.session_state.medsym_lines = []
    _bump("medsym_rev")
    open_dialog("medsym")


def _medsym_dismiss():
    close_dialog()
    st.session_state.pop("_medsym", None)
    st.session_state.medsym_lines = []


@st.dialog("Log the symptom too?", on_dismiss=_medsym_dismiss)
def medsym_dialog():
    ss = st.session_state
    ctx = ss.get("_medsym") or {}
    meds = ctx.get("meds") or []
    d = date.fromisoformat(ctx.get("date", date.today().isoformat()))
    st.markdown(f"You took **{', '.join(meds)}** for a symptom. Which one?")
    symptom_picker("medsym_lines", "medsym")
    c1, c2 = st.columns([3, 1.3], vertical_alignment="bottom")
    new = c1.text_input("Or a new symptom", key=f"medsym_new_{ss.get('medsym_rev', 0)}",
                        placeholder="e.g. brain fog")
    if c2.button("Add", key="medsym_new_add", width='stretch') and new.strip():
        name = ensure_symptom_type(new.strip())
        if name not in {l["name"] for l in ss.medsym_lines}:
            ss.medsym_lines.append(_sym_new_line(name))
        _bump("medsym_rev")
        st.rerun(scope="fragment")
    for line in list(ss.medsym_lines):
        symptom_editor(line, "medsym_lines", "medsym")
    missing = [l["name"] for l in ss.medsym_lines if not l["buckets"]]
    if missing:
        st.caption("Pick a time of day for: " + ", ".join(missing))
    c1, c2 = st.columns(2)
    if c1.button("Save symptom", type="primary", width='stretch', key="medsym_save",
                 disabled=not ss.medsym_lines or bool(missing)):
        db.events_insert(symptom_rows(ss.medsym_lines, d, {"treated_with": meds}))
        flash("Medication and symptom logged.")
        _medsym_dismiss()
        st.rerun()
    if c2.button("Skip", width='stretch', key="medsym_skip"):
        _medsym_dismiss()
        st.rerun()


def _add_item(kind: str, d: date, regimens: list[dict], *, pick: str, typed: str,
              dose: float, unit: str, usual: bool, freq: str, brand: str,
              reason: str, purpose: str | None = None) -> str | None:
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
            "detail": {"kind": "extra", "supplement_id": sid,
                       **({"purpose": purpose} if purpose else {})}}])
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
    purpose = None
    if kind == "medication" and not usual:
        st.caption("Taken why?")
        purpose = purpose_picker(f"{p}_purpose", None)
    if st.button("Add", type="primary", width='stretch', key=f"{p}_go"):
        err = _add_item(kind, d, regimens, pick=pick, typed=typed or "", dose=dose,
                        unit=(unit or "").strip(), usual=usual, freq=freq,
                        brand=(brand or "").strip(), reason=(reason or "").strip(),
                        purpose=purpose)
        if err:
            st.warning(err)
        else:
            close_dialog()
            if purpose == "symptom":
                st.session_state.flash = None
                name = (typed if pick == _NEW else pick).strip()
                start_symptom_followup(d, [name])
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
        elif dialog_is("medsym"):
            medsym_dialog()
        st.markdown(f"**What do you usually take?**  \nAdd each {noun} you take regularly "
                    "and how often. From then on they're assumed taken unless you say "
                    "otherwise.")
        return

    daily = [r for r in regimens if C.is_daily(r.get("frequency"))]
    occasional = [r for r in regimens if not C.is_daily(r.get("frequency"))]

    # What was already recorded for this day, keyed by regimen id (older rows by name).
    existing = day_events(d, kind)
    skipped, dose_seen, taken, extras, stored = set(), {}, set(), [], set()
    purpose_seen: dict[str, str] = {}
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
            if det.get("purpose"):
                purpose_seen[key] = det["purpose"]
        elif k == "extra" or (k == "exception" and det.get("exception") == "extra"):
            extras.append(e)
        if k in ("skipped", "dose_changed", "taken"):
            stored.add((k, key, e.get("quantity") if k != "skipped" else None,
                        det.get("purpose") if k == "taken" else None))

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
                    wanted.add(("skipped", r["id"], None, None))
                elif abs(dose - base) > 1e-9:
                    rows.append({"item": r["name"], "quantity": dose, "unit": r.get("unit"),
                                 "detail": {"kind": "dose_changed", "regimen_id": r["id"],
                                            "usual_dose": base}})
                    wanted.add(("dose_changed", r["id"], dose, None))

        if occasional:
            section("Weekly & as needed — check if taken")
            for r in occasional:
                with st.container(key=f"rowgroup_{r['id']}"):
                    on, dose, _ = item_row(r, has(taken, r))
                    purpose = None
                    if on and kind == "medication":
                        purpose = purpose_picker(
                            f"pp_{kind}_{d}_{r['id']}",
                            purpose_seen.get(r["id"], purpose_seen.get(r["name"])))
                if on:
                    rows.append({"item": r["name"], "quantity": dose, "unit": r.get("unit"),
                                 "detail": {"kind": "taken", "regimen_id": r["id"],
                                            "frequency": r.get("frequency"),
                                            **({"purpose": purpose} if purpose else {})}})
                    wanted.add(("taken", r["id"], dose, purpose))

        if extras:
            logged_header("Also taken this day", key="loghead_extras")
            for e in extras:
                c1, c2 = st.columns([5, 0.7], vertical_alignment="center")
                amt = f" · {fmt_qty(e['quantity'])} {e.get('unit') or ''}".rstrip() \
                    if e.get("quantity") else ""
                why = PURPOSE_LABELS.get((e.get("detail") or {}).get("purpose"), "")
                c1.write(f"{e['item']}{amt}" + (f" · {why}" if why else ""))
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
        # Newly marked "for a symptom": offer to log the symptom straight away
        # (the pop-up is the confirmation then, so no toast on top of it).
        before = {t[1] for t in stored if t[0] == "taken" and t[3] == "symptom"}
        newly = [r["item"] for r in rows if r["detail"].get("purpose") == "symptom"
                 and r["detail"]["regimen_id"] not in before]
        if newly:
            start_symptom_followup(d, newly)
        else:
            flash("Saved — " + (", ".join(b for b in bits if b)
                                or "your usual list stands for this day") + ".")
        st.rerun()
    if add:
        open_dialog(f"add_{kind}")
    if dialog_is(f"add_{kind}"):
        _ADD_DIALOGS[kind](kind, d, regimens, False)
    elif dialog_is("medsym"):
        medsym_dialog()


def panel_supplements():
    panel_regimen("supplement")


def panel_medications():
    panel_regimen("medication")


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

# Habits come in two flavours, chosen per habit:
#   * plain: nothing is assumed; tick it (or enter a count) on the days you did it.
#   * usual: assumed done on its scheduled days from a start date, the way the
#     usual supplement list works. Only days that differ get a row.
# The schedule lives in app_settings under one key (JSON keyed by habit id), so
# it needed no database change. When a schedule is switched off or changed,
# the days it covered are first written out as ordinary rows ("frozen"), so
# history never changes after the fact.

HABIT_PLANS_KEY = "habit_usual"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HABIT_FREQS = {"every day": list(range(7)), "weekdays": [0, 1, 2, 3, 4], "pick days": None}
HABIT_KIND_LABELS = {"binary": "yes / no", "count": "count"}


def habit_plans() -> dict:
    ss = st.session_state
    if "_habit_plans" not in ss:
        raw = db.setting_get(HABIT_PLANS_KEY)
        try:
            ss._habit_plans = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            ss._habit_plans = {}
    return ss._habit_plans


def save_habit_plans(plans: dict):
    db.setting_set(HABIT_PLANS_KEY, json.dumps(plans))
    st.session_state._habit_plans = plans


def habit_assumed(h: dict, d: date, plans: dict | None = None) -> float | None:
    """The value a usual habit is assumed to have on day d, or None if nothing
    is assumed that day (plain habit, before its start, or not a scheduled day)."""
    p = (plans if plans is not None else habit_plans()).get(h["id"])
    if not p or d < date.fromisoformat(p["since"]) or d.weekday() not in p.get("days", []):
        return None
    return float(p.get("amount") or 1) if h["kind"] == "count" else 1.0


def describe_days(days: list[int]) -> str:
    days = sorted(days or [])
    for label, ds in HABIT_FREQS.items():
        if ds == days:
            return label
    return ", ".join(WEEKDAYS[i] for i in days)


def describe_plan(h: dict, p: dict) -> str:
    since = date.fromisoformat(p["since"])
    amt = f", usually {float(p.get('amount') or 1):g} {h.get('unit') or ''}".rstrip() \
        if h["kind"] == "count" else ""
    return f"usually done {describe_days(p.get('days'))}{amt} · since {since:%b} {since.day}"


def habit_value(h: dict, d: date, explicit: dict, plans: dict) -> tuple[float | None, str]:
    """(value, source) for one habit on one day. source is 'logged', 'usual' or
    '' (nothing known). `explicit` maps habit name -> that day's row."""
    e = explicit.get(h["name"])
    if e is not None:
        return float(e.get("quantity") or 0), "logged"
    a = habit_assumed(h, d, plans)
    if a is not None:
        return a, "usual"
    return None, ""


def freeze_habit(h: dict, plan: dict, upto: date):
    """Write out the days a usual schedule covered (since .. the day before
    `upto`) as ordinary rows, wherever the day doesn't already have one."""
    start, end = date.fromisoformat(plan["since"]), upto - timedelta(days=1)
    if end < start:
        return
    have = {str(e["event_date"])[:10]
            for e in db.events_fetch(start=start, end=end, category="habit", limit=100000)
            if e.get("item") == h["name"]}
    amount = float(plan.get("amount") or 1) if h["kind"] == "count" else 1.0
    rows, day = [], start
    while day <= end:
        if day.weekday() in plan.get("days", []) and day.isoformat() not in have:
            rows.append({"event_date": day, "category": "habit", "item": h["name"],
                         "quantity": amount, "unit": h.get("unit") or (
                             "done" if h["kind"] == "binary" else None),
                         "detail": {"kind": h["kind"], "from_usual": True}})
        day += timedelta(days=1)
    db.events_insert(rows)


def set_habit_plan(h: dict, new: dict | None):
    """Switch a habit's usual schedule on, off, or to a new shape."""
    plans = dict(habit_plans())
    old = plans.get(h["id"])
    if old and new and old.get("days") == new.get("days") \
            and old.get("amount") == new.get("amount"):
        return                                    # nothing changed
    if old:
        freeze_habit(h, old, date.today())
        if new:
            new = {**new, "since": date.today().isoformat()}
    if new:
        plans[h["id"]] = new
    else:
        plans.pop(h["id"], None)
    save_habit_plans(plans)


def habit_usual_inputs(prefix: str, kind: str, *, current: dict | None = None,
                       start_default: date | None = None):
    """'I usually do this' + how often (+ usual amount for counts). Returns
    (usual?, plan-without-since, start date or None, problem or None)."""
    usual = st.checkbox("I usually do this", value=bool(current), key=f"{prefix}_usual",
                        help="It's checked automatically on the days you pick. "
                             "Uncheck it on days you didn't.")
    if not usual:
        return False, None, None, None
    days0 = (current or {}).get("days", HABIT_FREQS["every day"])
    freq0 = next((k for k, v in HABIT_FREQS.items() if v == sorted(days0)), "pick days")
    freq = st.segmented_control("How often", list(HABIT_FREQS), default=freq0,
                                key=f"{prefix}_freq") or "every day"
    if freq == "pick days":
        picked = st.pills("Days", WEEKDAYS, selection_mode="multi", key=f"{prefix}_days",
                          default=[WEEKDAYS[i] for i in days0] if freq0 == "pick days" else None)
        days = [WEEKDAYS.index(x) for x in (picked or [])]
    else:
        days = HABIT_FREQS[freq]
    amount = None
    if kind == "count":
        amount = st.number_input("Usual amount", min_value=0.0, step=1.0, format="%g",
                                 value=float((current or {}).get("amount") or 1),
                                 key=f"{prefix}_amount")
    start = None
    if start_default is not None:
        start = st.date_input("Starting", value=start_default, max_value=date.today(),
                              format="MM/DD/YYYY", key=f"{prefix}_start")
    problem = None if days else "Pick at least one day."
    return True, {"days": sorted(days), **({"amount": amount} if amount is not None else {})}, \
        start, problem


@st.dialog("New habit", on_dismiss=close_dialog)
def new_habit_dialog(d: date):
    name = st.text_input("Habit name", placeholder="e.g. stretch", key="nh_name")
    kind_lbl = st.segmented_control("Type", list(HABIT_KIND_LABELS.values()),
                                    default="yes / no", key="nh_kind") or "yes / no"
    kind = "count" if kind_lbl == "count" else "binary"
    unit, goal = "", None
    if kind == "count":
        c1, c2 = st.columns(2)
        unit = c1.text_input("Unit", placeholder="glasses, min", key="nh_unit")
        goal = c2.number_input("Goal (optional)", min_value=0.0, step=1.0, format="%g",
                               value=None, key="nh_goal")
    usual, plan, _, problem = habit_usual_inputs("nh", kind)
    if usual:
        st.caption(f"Counted as done from {d:%b} {d.day} (the day you're on). "
                   "To start earlier, go back to that day first.")
    # Never disabled: on a phone the typed name only reaches the app when the
    # box loses focus, so a button waiting for it would swallow the first tap.
    if st.button("Add habit", type="primary", width='stretch', key="nh_go"):
        name = (name or "").strip()
        everything = db.ref_list("habits", active_only=False)
        match = next((h for h in everything if h["name"].lower() == name.lower()), None)
        if not name:
            st.warning("Give the habit a name.")
        elif match and match.get("active", True):
            st.warning(f"{match['name']} is already on your list.")
        elif problem:
            st.warning(problem)
        else:
            row = {"name": name, "kind": kind, "unit": (unit or "").strip() or None,
                   "target": goal, "active": True, "sort_order": len(everything)}
            if match:                      # a retired habit with this name: bring it back
                row = {**match, **row, "sort_order": match.get("sort_order", 0)}
            hid = db.ref_upsert("habits", row)
            if usual:
                plans = dict(habit_plans())
                plans[hid] = {**plan, "since": d.isoformat()}
                save_habit_plans(plans)
            bust_refs()
            flash(f"Added {name}.")
            close_dialog()
            st.rerun()


def habit_rows_for_day(habits: list[dict], d: date, values: dict[str, float],
                       plans: dict) -> list[dict]:
    """Rows to store for one day. Usual habits only get a row when the day
    differs from usual; plain habits always get one (0 = didn't)."""
    rows = []
    for h in habits:
        v = values[h["name"]]
        a = habit_assumed(h, d, plans)
        if a is not None and abs(v - a) < 1e-9:
            continue
        detail = {"kind": h["kind"], **({"usual": a} if a is not None else {})}
        rows.append({"event_date": d, "time_bucket": None, "category": "habit",
                     "item": h["name"], "quantity": v,
                     "unit": h.get("unit") or ("done" if h["kind"] == "binary" else None),
                     "detail": detail})
    return rows


def panel_habits():
    d = header("Habits")
    habits = ref_rows("habits")
    plans = habit_plans()
    bar = action_bar()

    if dialog_is("new_habit"):
        new_habit_dialog(d)

    if not habits:
        with bar:
            if st.button("＋ New habit", width='stretch', key="add_btn"):
                open_dialog("new_habit")
                st.rerun()
        st.markdown("**What habits do you want to track?**  \nAdd each one as a yes/no "
                    "or a count. Habits you usually do can be checked automatically.")
        return

    # Only rows for habits on the list: a retired habit's old rows are left alone.
    names = {h["name"] for h in habits}
    existing_rows = [e for e in day_events(d, "habit") if e["item"] in names]
    existing = {e["item"]: e for e in existing_rows}
    usual_today = [h for h in habits if habit_assumed(h, d, plans) is not None]
    plain_today = [h for h in habits if h not in usual_today]

    values: dict[str, float] = {}

    def habit_row(h: dict):
        v, _ = habit_value(h, d, existing, plans)
        # The date is part of every key: otherwise a box ticked on one day
        # kept its tick when you moved to another day.
        if h["kind"] == "binary":
            (c1,) = st.columns(1)     # a column row, so it gets the row divider
            values[h["name"]] = 1.0 if c1.checkbox(
                h["name"], value=bool(v), key=f"hb_{d}_{h['id']}") else 0.0
        else:
            c1, c2, c3 = st.columns([4.6, 1.35, 1.25], vertical_alignment="center")
            target = f" <span class='unit'>goal {h['target']:g}</span>" if h.get("target") else ""
            c1.markdown(f"{h['name']}{target}", unsafe_allow_html=True)
            values[h["name"]] = float(c2.number_input(
                h["name"], min_value=0.0, step=1.0, format="%g",
                value=float(v or 0), key=f"hc_{d}_{h['id']}", label_visibility="collapsed"))
            c3.markdown(f"<span class='unit'>{h.get('unit') or ''}</span>",
                        unsafe_allow_html=True)

    with st.container(key="list"):
        if usual_today:
            section("Usually done — uncheck if you didn't")
            for h in usual_today:
                habit_row(h)
        if plain_today:
            if usual_today:
                section("Check what you did")
            for h in plain_today:
                habit_row(h)

    rows = habit_rows_for_day(habits, d, values, plans)
    wanted = {(r["item"], float(r["quantity"])) for r in rows}
    by_name = {h["name"]: h for h in habits}
    # A stored row that just repeats the usual value says nothing new.
    stored = {(n, float(e.get("quantity") or 0)) for n, e in existing.items()
              if habit_assumed(by_name[n], d, plans) != float(e.get("quantity") or 0)}
    changed = wanted != stored
    with bar:
        c1, c2 = st.columns(2)
        label = ("No changes" if not changed else "Save changes" if existing else "Save day")
        save = c1.button(label, type="primary", width='stretch', key="save_btn",
                         disabled=not changed)
        if c2.button("＋ New habit", width='stretch', key="add_btn"):
            open_dialog("new_habit")
            st.rerun()
    if save:
        for e in existing_rows:
            db.event_delete(e["id"])
        db.events_insert(rows)
        flash("Habits saved.")
        st.rerun()


# ---------------------------------------------------------------------------
# Weekly review
# ---------------------------------------------------------------------------
# Four short steps: what the week looks like, fill the gaps, how complete
# each category was, anything unusual. Saved as one `events` row with
# category 'review', dated the week's Monday; the answers live in `detail`:
#   completeness  {category: {"band": "80–100%", "low": 80, "high": 100}}
#   gaps          {"alcohol"|"symptom": {"YYYY-MM-DD": "none"|"unsure"}}
#                 (days with nothing logged: "none" = it didn't happen)
#   usual_check   {"supplement"|"medication": "all"|"missed"}
#   tags, other_note
# Answers survive jumping out to fix a day: they're kept in a per-week draft.

REVIEW_STEPS = ["Your week", "Fill the gaps", "How complete?", "Anything unusual?"]
CATEGORY_LABELS = {"food": "Food & drink", "alcohol": "Alcohol", "symptom": "Symptoms",
                   "supplement": "Supplements", "medication": "Medications",
                   "habit": "Habits"}
GAP_CATEGORIES = ("alcohol", "symptom")
GAP_CHOICES = {"none": "none", "not sure": "unsure"}
USUAL_CHOICES = {"all taken as listed": "all", "missed some": "missed"}


def band_label(lo: int, hi: int) -> str:
    return f"{lo}–{hi}%"


# Shown without the % sign so all five fit on one line; stored with it.
BAND_BY_LABEL = {f"{lo}–{hi}": (lo, hi) for lo, hi in C.COMPLETENESS_BANDS}
_BAND_SHOWN = {band_label(lo, hi): f"{lo}–{hi}" for lo, hi in C.COMPLETENESS_BANDS}


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def review_target(today: date | None = None) -> date:
    """The week the review card offers: the one ending today on a Sunday,
    otherwise last week."""
    today = today or date.today()
    ws = week_start(today)
    return ws if today.weekday() == 6 else ws - timedelta(days=7)


def week_label(ws: date) -> str:
    we = ws + timedelta(days=6)
    tail = f"{we.day}" if we.month == ws.month else f"{we:%b} {we.day}"
    return f"{ws:%b} {ws.day} – {tail}"


def reviewed_weeks() -> set[str]:
    ss = st.session_state
    if "_reviewed" not in ss:
        ss._reviewed = {str(e["event_date"])[:10]
                        for e in db.events_fetch(category=C.REVIEW_CATEGORY, limit=1000)}
    return ss._reviewed


def jump(cat: str, d: date):
    """Open a panel on a given day, with the back arrow returning to the review."""
    st.session_state.log_date = d
    st.session_state._back_to = "review"
    go(cat)


def _edate(e: dict) -> date:
    return date.fromisoformat(str(e["event_date"])[:10])


def _days(ds) -> str:
    return ", ".join(f"{d:%a}" for d in sorted(set(ds)))


def _week_regimens(kind: str, ws: date, we: date, daily_only: bool = False) -> list[dict]:
    """Regimens of a kind in force at any point in the week (from the cached lists)."""
    cat = {s["id"]: s for s in ref_rows("supplements") if kind_of(s) == kind}
    out = []
    for r in ref_rows("supplement_regimens"):
        if r["supplement_id"] not in cat or r.get("status") == "paused":
            continue
        if str(r["start_date"])[:10] > we.isoformat():
            continue
        if r.get("end_date") and str(r["end_date"])[:10] < ws.isoformat():
            continue
        if daily_only and not C.is_daily(r.get("frequency")):
            continue
        out.append({**r, "name": cat[r["supplement_id"]]["name"]})
    return out


def _regimen_summary(kind: str, evs: list[dict], ws: date, we: date) -> list[str]:
    by: dict[tuple[str, str], list[date]] = {}
    for e in evs:
        det = e.get("detail") or {}
        k = det.get("kind")
        if k == "exception":
            k = det.get("exception")
        label = {"skipped": "skipped", "dose_changed": "dose changed",
                 "taken": "took", "extra": "extra"}.get(k)
        if label and e.get("item"):
            by.setdefault((label, e["item"]), []).append(_edate(e))
    lines = [f"{label} {item} · {_days(ds)}" for (label, item), ds in sorted(by.items())]
    if not lines:
        if _week_regimens(kind, ws, we):
            return ["usual list every day"]
        return ["nothing set up"]
    return lines


def _habit_summary(evs: list[dict], days: list[date]) -> list[str]:
    habits = ref_rows("habits")
    if not habits:
        return ["no habits yet"]
    plans = habit_plans()
    by_day: dict[date, dict] = {}
    for e in evs:
        by_day.setdefault(_edate(e), {})[e["item"]] = e
    lines = []
    for h in habits:
        vals = [habit_value(h, d, by_day.get(d, {}), plans)[0] for d in days]
        known = [v for v in vals if v is not None]
        blank = len(vals) - len(known)
        tail = f" · {blank} blank" if blank else ""
        if h["kind"] == "binary":
            lines.append(f"{h['name']} · done {sum(1 for v in known if v > 0)} of "
                         f"{len(days)} days{tail}")
        else:
            total = sum(known)
            lines.append(f"{h['name']} · {total:g} {h.get('unit') or ''} total".rstrip()
                         + tail)
    return lines


def week_summary(ws: date, by_cat: dict[str, list[dict]], days: list[date]) -> dict[str, list[str]]:
    we = ws + timedelta(days=6)
    out: dict[str, list[str]] = {}

    food = by_cat.get("food", [])
    fd = {_edate(e) for e in food}
    out["food"] = [f"{len(food)} entr{'ies' if len(food) != 1 else 'y'} on {len(fd)} of "
                   f"{len(days)} days"] if food \
        else ["nothing logged"]

    alc = by_cat.get("alcohol", [])
    per: dict[date, int] = {}
    for e in alc:
        per[_edate(e)] = per.get(_edate(e), 0) + int(e.get("quantity") or 0)
    if alc:
        total = sum(per.values())
        detail = ", ".join(f"{d:%a} {n}" for d, n in sorted(per.items()))
        out["alcohol"] = [f"{total} drink{'s' if total != 1 else ''} on {len(per)} "
                          f"day{'s' if len(per) != 1 else ''} · {detail}"]
    else:
        out["alcohol"] = ["nothing logged"]

    entries: dict[str, dict] = {}
    for e in by_cat.get("symptom", []):
        gid = (e.get("detail") or {}).get("entry_id") or e["id"]
        entries.setdefault(gid, e)
    per_sym: dict[str, list[dict]] = {}
    for e in entries.values():
        per_sym.setdefault(e["item"], []).append(e)
    out["symptom"] = [
        f"{name} · {_days(_edate(e) for e in es)} · worst {max(e.get('severity') or 0 for e in es)}/5"
        for name, es in sorted(per_sym.items())] or ["nothing logged"]

    out["supplement"] = _regimen_summary("supplement", by_cat.get("supplement", []), ws, we)
    out["medication"] = _regimen_summary("medication", by_cat.get("medication", []), ws, we)
    out["habit"] = _habit_summary(by_cat.get("habit", []), days)
    return out


def _rv_init(key: str, draft: dict, saved, default=None):
    """Give a review widget its starting value: the draft (answers given before
    jumping out to fix a day), else the saved review, else the default."""
    ss = st.session_state
    if key in ss:
        return
    if key in draft:
        ss[key] = draft[key]
    elif saved is not None:
        ss[key] = saved
    elif default is not None:
        ss[key] = default


def _review_add_body(kind: str, ws: date):
    days = [ws + timedelta(days=i) for i in range(7) if ws + timedelta(days=i) <= date.today()]
    labels = {f"{d:%a} {d:%b} {d.day}": d for d in days}
    pick = st.pills("Which day?", list(labels), selection_mode="single",
                    default=list(labels)[0], key=f"rva_{kind}_{ws}_day")
    d = labels.get(pick) or days[0]
    st.caption("If it's part of your usual routine, this is the day it starts.")
    _add_dialog_body(kind, d, db.regimens_on(d, kind), False)


_REVIEW_ADD = {k: st.dialog(f"Add a {v['noun']}", on_dismiss=close_dialog)(_review_add_body)
               for k, v in REGIMEN_KINDS.items()}


def _day_picker(label: str, cat: str, days: list[date], key: str):
    """'Edit' popover: tap a day to open that panel on that day."""
    with st.popover(label, width='stretch'):
        for d in days:
            if st.button(day_label(d), key=f"{key}_{d}", width='stretch'):
                jump(cat, d)


def panel_review():
    ss = st.session_state
    topbar("Weekly review")
    ws = ss.get("review_week") or review_target()
    we = ws + timedelta(days=6)
    step = ss.setdefault("review_step", 0)

    with st.container(key="datebar"):
        c1, c2, c3 = st.columns([1, 4.2, 1], vertical_alignment="center")
        if c1.button("◀", key="prev_week", width='stretch', help="Previous week"):
            ss.review_week, ss.review_step = ws - timedelta(days=7), 0
            st.rerun()
        done = ws.isoformat() in reviewed_weeks()
        c2.markdown(f"<div class='ptitle' style='font-size:1.05rem'>{week_label(ws)}"
                    f"{' ✓' if done else ''}</div>", unsafe_allow_html=True)
        if c3.button("▶", key="next_week", width='stretch', help="Next week",
                     disabled=ws + timedelta(days=7) > review_target()):
            ss.review_week, ss.review_step = ws + timedelta(days=7), 0
            st.rerun()
    show_flash()

    days = [ws + timedelta(days=i) for i in range(7) if ws + timedelta(days=i) <= date.today()]
    evs = db.events_fetch(start=ws, end=we, limit=5000)
    by_cat: dict[str, list[dict]] = {}
    for e in evs:
        by_cat.setdefault(e["category"], []).append(e)
    saved_row = next((e for e in by_cat.get(C.REVIEW_CATEGORY, [])
                      if _edate(e) == ws), None)
    saved = (saved_row or {}).get("detail") or {}
    drafts = ss.setdefault("review_draft", {})
    draft = drafts.setdefault(ws.isoformat(), {})
    rendered: list[str] = []          # widget keys drawn this run, mirrored into the draft

    has_meds = bool(_week_regimens("medication", ws, we) or by_cat.get("medication"))
    has_habits = bool(ref_rows("habits"))
    cats = [c for c in CATEGORY_LABELS
            if (c != "medication" or has_meds) and (c != "habit" or has_habits)]

    st.markdown(f"<div class='steplabel'>Step {step + 1} of {len(REVIEW_STEPS)} · "
                f"<b>{REVIEW_STEPS[step]}</b></div>", unsafe_allow_html=True)
    bar = action_bar()

    # -- 1. the week at a glance ---------------------------------------------
    if step == 0:
        summary = week_summary(ws, by_cat, days)
        with st.container(key="list_review"):
            for cat in cats:
                c1, c2 = st.columns([4.4, 1.5], vertical_alignment="center")
                c1.markdown(f"**{CATEGORY_LABELS[cat]}**  \n" + "  \n".join(
                    f"<span class='ref'>{line}</span>" for line in summary[cat]),
                    unsafe_allow_html=True)
                with c2:
                    _day_picker("Edit", cat, days, f"rved_{cat}")
                    if cat in REGIMEN_KINDS and st.button(
                            "＋ Add", key=f"rvadd_{cat}", width='stretch'):
                        open_dialog(f"rvadd_{cat}")
                        st.rerun()

    # -- 2. fill the gaps -----------------------------------------------------
    gap_days: dict[str, list[date]] = {}
    for cat in GAP_CATEGORIES:
        logged = {_edate(e) for e in by_cat.get(cat, [])}
        gap_days[cat] = [d for d in days if d not in logged]

    def gap_key(cat, d):
        return f"rv_{ws}_gap_{cat}_{d}"

    def mark_all_none(cat):
        for d in gap_days[cat]:
            st.session_state[gap_key(cat, d)] = "none"

    if step == 1:
        any_gap = False
        for cat in GAP_CATEGORIES:
            if not gap_days[cat]:
                continue
            any_gap = True
            c1, c2 = st.columns([3, 2], vertical_alignment="center")
            c1.markdown(f"**{CATEGORY_LABELS[cat]}** — nothing logged on these days",
                        unsafe_allow_html=True)
            c2.button("All none", key=f"rv_allnone_{cat}", width='stretch',
                      on_click=mark_all_none, args=(cat,))
            saved_gaps = (saved.get("gaps") or {}).get(cat, {})
            label_of = {v: k for k, v in GAP_CHOICES.items()}
            with st.container(key=f"list_gap_{cat}"):
                for d in gap_days[cat]:
                    k = gap_key(cat, d)
                    _rv_init(k, draft, label_of.get(saved_gaps.get(d.isoformat())))
                    g1, g2, g3 = st.columns([1.6, 3.4, 0.9], vertical_alignment="center")
                    g1.markdown(f"{d:%a} {d.day}")
                    g2.pills(f"{cat} {d}", list(GAP_CHOICES), selection_mode="single",
                             key=k, label_visibility="collapsed")
                    rendered.append(k)
                    if g3.button("＋", key=f"rv_gapadd_{cat}_{d}", width='stretch',
                                 help="Log it now"):
                        jump(cat, d)
        if not any_gap:
            st.caption("No gaps — alcohol and symptoms have something logged every day.")

        for kind in REGIMEN_KINDS:
            if not _week_regimens(kind, ws, we, daily_only=True):
                continue
            k = f"rv_{ws}_usual_{kind}"
            label_of = {v: x for x, v in USUAL_CHOICES.items()}
            _rv_init(k, draft, label_of.get((saved.get("usual_check") or {}).get(kind)))
            st.markdown(f"**{CATEGORY_LABELS[kind]}** — any days you missed your usual?")
            st.pills(f"usual {kind}", list(USUAL_CHOICES), selection_mode="single", key=k,
                     label_visibility="collapsed")
            rendered.append(k)
            if ss.get(k) == "missed some":
                _day_picker("Fix a day", kind, days, f"rvfix_{kind}")

    # -- 3. completeness ------------------------------------------------------
    def comp_key(cat):
        return f"rv_{ws}_comp_{cat}"

    def hint(cat) -> str:
        n = len(days)
        if cat == "food":
            return f"entries on {len({_edate(e) for e in by_cat.get('food', [])})} of {n} days"
        if cat in GAP_CATEGORIES:
            covered = n - len(gap_days[cat]) + sum(
                1 for d in gap_days[cat]
                if (ss.get(gap_key(cat, d)) or draft.get(gap_key(cat, d))) == "none")
            return f"logged or marked none on {covered} of {n} days"
        if cat in REGIMEN_KINDS:
            return "your usual list is assumed"
        plans = habit_plans()
        if any(h["id"] in plans for h in ref_rows("habits")):
            return "usual habits are assumed"
        return f"something logged on {len({_edate(e) for e in by_cat.get('habit', [])})} of {n} days"

    if step == 2:
        st.caption("Roughly what % of what happened made it into the log? This tells "
                   "later analyses how far to trust each week.")
        with st.container(key="list_comp"):
            for cat in cats:
                k = comp_key(cat)
                _rv_init(k, draft, _BAND_SHOWN.get(
                    ((saved.get("completeness") or {}).get(cat) or {}).get("band")))
                st.markdown(f"**{CATEGORY_LABELS[cat]}** <span class='ref'>· {hint(cat)}</span>",
                            unsafe_allow_html=True)
                st.pills(f"completeness {cat}", list(BAND_BY_LABEL), selection_mode="single",
                         key=k, label_visibility="collapsed")
                rendered.append(k)

    # -- 4. anything unusual --------------------------------------------------
    tag_key, other_key = f"rv_{ws}_tags", f"rv_{ws}_other"
    if step == 3:
        options = C.REVIEW_TAGS + ["other"]
        saved_tags = [t for t in (saved.get("tags") or []) if t in options]
        if saved.get("other_note") and "other" not in saved_tags:
            saved_tags.append("other")
        _rv_init(tag_key, draft, saved_tags or None)
        st.caption("Optional — anything that made this week different.")
        st.pills("tags", options, selection_mode="multi", key=tag_key,
                 label_visibility="collapsed")
        rendered.append(tag_key)
        if "other" in (ss.get(tag_key) or []):
            _rv_init(other_key, draft, saved.get("other_note"))
            st.text_input("What else?", key=other_key, placeholder="a few words")
            rendered.append(other_key)

    for k in rendered:
        draft[k] = ss.get(k)

    # -- navigation + save ----------------------------------------------------
    def val(k):
        return ss[k] if k in ss and k in rendered else draft.get(k)

    with bar:
        c1, c2 = st.columns(2)
        if c1.button("◀ Back", key="rv_back", width='stretch', disabled=step == 0):
            ss.review_step = step - 1
            st.rerun()
        last = step == len(REVIEW_STEPS) - 1
        if not last:
            if c2.button("Next ▶", key="rv_next", type="primary", width='stretch'):
                ss.review_step = step + 1
                st.rerun()
        elif c2.button("Update review" if saved_row else "Save review", key="rv_save",
                       type="primary", width='stretch'):
            completeness = {}
            for cat in cats:
                lbl = val(comp_key(cat))
                if lbl in BAND_BY_LABEL:
                    lo, hi = BAND_BY_LABEL[lbl]
                    completeness[cat] = {"band": band_label(lo, hi), "low": lo, "high": hi}
            gaps = {}
            for cat in GAP_CATEGORIES:
                answers = {d.isoformat(): GAP_CHOICES[val(gap_key(cat, d))]
                           for d in gap_days[cat] if val(gap_key(cat, d)) in GAP_CHOICES}
                if answers:
                    gaps[cat] = answers
            usual = {kind: USUAL_CHOICES[val(f"rv_{ws}_usual_{kind}")]
                     for kind in REGIMEN_KINDS
                     if val(f"rv_{ws}_usual_{kind}") in USUAL_CHOICES}
            tags = [t for t in (val(tag_key) or []) if t != "other"]
            other = (val(other_key) or "").strip() if "other" in (val(tag_key) or []) else ""
            if saved_row:
                db.event_delete(saved_row["id"])
            db.events_insert([{
                "event_date": ws, "category": C.REVIEW_CATEGORY, "item": "weekly",
                "detail": {"week_start": ws.isoformat(), "week_end": we.isoformat(),
                           "completeness": completeness, "gaps": gaps,
                           "usual_check": usual, "tags": tags,
                           "other_note": other or None}}])
            drafts.pop(ws.isoformat(), None)
            for k in [k for k in ss.keys() if str(k).startswith(f"rv_{ws}_")]:
                del ss[k]
            ss.pop("_reviewed", None)
            ss.review_step = 0
            flash(f"Week of {week_label(ws)} reviewed.")
            go(None)

    for kind in REGIMEN_KINDS:
        if dialog_is(f"rvadd_{kind}"):
            _REVIEW_ADD[kind](kind, ws)
    if dialog_is("medsym"):
        medsym_dialog()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _added_on(created_at) -> date | None:
    """The local date a reference row was created, from its created_at stamp."""
    if not created_at:
        return None
    try:
        ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (ts.astimezone() if ts.tzinfo else ts).date()


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
            picked = st.time_input(slot.title(), value=slot_default_time(slot), **TIME_FMT,
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
        st.caption("Add new habits on the Habits page. Here you can change whether "
                   "one is usually done, or remove it.")
        plans = habit_plans()
        with st.container(key="list_hab"):
            for h in ref_rows("habits"):
                p = plans.get(h["id"])
                added = _added_on(h.get("created_at"))
                unit = f" ({h['unit']})" if h.get("unit") else ""
                meta = describe_plan(h, p) if p else (f"added {added:%b} {added.day}"
                                                      if added else "")
                c1, c2, c3 = st.columns([4.2, 1.3, 0.7], vertical_alignment="center")
                c1.markdown(f"**{h['name']}** <span class='unit'>"
                            f"{HABIT_KIND_LABELS.get(h['kind'], h['kind'])}{unit}</span>"
                            f"  \n<span class='ref'>{meta}</span>", unsafe_allow_html=True)
                with c2.popover("Edit", width='stretch'):
                    usual, plan, start, problem = habit_usual_inputs(
                        f"eh_{h['id']}", h["kind"], current=p,
                        start_default=None if p else (added or date.today()))
                    if p:
                        st.caption("Changes apply from today. Days already covered "
                                   "keep what they were.")
                    if st.button("Save", type="primary", width='stretch',
                                 key=f"eh_{h['id']}_go"):
                        if problem:
                            st.warning(problem)
                        else:
                            if usual and not p:
                                plan = {**plan, "since": (start or date.today()).isoformat()}
                            set_habit_plan(h, plan if usual else None)
                            flash(f"{h['name']} updated.")
                            st.rerun()
                if c3.button("✕", key=f"dh_{h['id']}", width='stretch'):
                    db.ref_delete("habits", h["id"])
                    if p:
                        save_habit_plans({k: v for k, v in plans.items() if k != h["id"]})
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
    "review": panel_review,
    "settings": panel_settings,
}

ROUTES.get(st.session_state.panel, home)()
flush_device_store()
