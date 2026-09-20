"""Health tracker -- beta.

Design rules this app is built around:
  * Logging is retrospective. Every panel opens on a date you can change.
  * Time of day is always prefilled from the clock and always editable.
  * Nothing is typed that can be tapped.
  * Every panel writes into one `events` table (see schema.sql).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd
import streamlit as st

import config as C
from db import get_db

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
    if not types:
        st.info("No symptoms defined yet. Add them in Settings.")
        return

    st.caption("Tap a symptom, set severity and when.")
    cols = st.columns(3)
    for i, t in enumerate(types):
        with cols[i % 3]:
            if st.button(t["name"], key=f"sym_{t['id']}", width='stretch'):
                st.session_state.sym_lines.append(
                    {"uid": str(uuid.uuid4())[:8], "name": t["name"],
                     "severity": 3, "bucket": C.current_bucket(), "note": ""}
                )
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
                line["bucket"] = bucket_picker(f"sb_{line['uid']}", line["bucket"])
                line["note"] = st.text_input(
                    "note", value=line["note"], key=f"sn_{line['uid']}",
                    placeholder="note (optional)", label_visibility="collapsed")

        if st.button(f"Save {len(lines)} symptom{'s' if len(lines) != 1 else ''}",
                     type="primary", width='stretch'):
            db.events_insert([
                {"event_date": d, "time_bucket": l["bucket"], "category": "symptom",
                 "item": l["name"], "severity": int(l["severity"]),
                 "note": l["note"].strip() or None}
                for l in lines
            ])
            st.session_state.sym_lines = []
            flash("Symptoms logged.")
            st.rerun()

    existing = day_events(d, "symptom")
    if existing:
        st.divider()
        st.markdown("**Already logged this day**")
        for e in existing:
            c1, c2 = st.columns([4, 0.8])
            note = f" — {e['note']}" if e.get("note") else ""
            c1.write(f"{e['item']} · severity {e['severity']} · {e['time_bucket']}{note}")
            if c2.button("✕", key=f"del_{e['id']}", width='stretch'):
                db.event_delete(e["id"])
                st.rerun()


# ---------------------------------------------------------------------------
# Supplements
# ---------------------------------------------------------------------------

def panel_supplements():
    d = header("Supplements")
    regimens = db.regimens_on(d)
    if not regimens:
        st.info("No active regimen for this date. Add your supplements in Settings.")
        return

    daily = [r for r in regimens if C.is_daily(r.get("frequency"))]
    occasional = [r for r in regimens if not C.is_daily(r.get("frequency"))]

    existing = day_events(d, "supplement")
    confirmed = any((e.get("detail") or {}).get("kind") == "day_confirmed"
                    for e in existing)
    prior_exc = {e["item"]: (e.get("detail") or {}).get("exception")
                 for e in existing if (e.get("detail") or {}).get("kind") == "exception"}
    prior_taken = {e["item"] for e in existing
                   if (e.get("detail") or {}).get("kind") == "taken"}

    st.caption(
        "One tap confirms you took your usual daily stack. Mark only what "
        "differed — a day with no confirmation counts as *unknown*, not a miss."
    )

    def caption_for(r):
        bits = []
        if r.get("dose"):
            bits.append(f"{r['dose']:g} {r.get('unit') or ''}".strip())
        if r.get("frequency"):
            bits.append(r["frequency"])
        if r.get("brand"):
            bits.append(r["brand"])
        return " · ".join(bits)

    exceptions: dict[str, str] = {}
    if daily:
        st.markdown("**Daily — anything different?**")
        for r in daily:
            with st.container(border=True):
                c1, c2 = st.columns([2.2, 2.0])
                c1.markdown(f"**{r['name']}**  \n<span class='ref'>{caption_for(r)}</span>",
                            unsafe_allow_html=True)
                opts = ["took it", "skipped", "extra"]
                prior = prior_exc.get(r["name"])
                choice = c2.radio("state", opts,
                                  index=opts.index(prior) if prior in opts else 0,
                                  key=f"sup_{r['id']}", horizontal=True,
                                  label_visibility="collapsed")
                if choice != "took it":
                    exceptions[r["name"]] = choice

    taken_occasional: list[dict] = []
    if occasional:
        st.markdown("**Not daily — tap if you took it today**")
        for r in occasional:
            with st.container(border=True):
                on = st.toggle(f"{r['name']} — {caption_for(r)}",
                               value=r["name"] in prior_taken, key=f"occ_{r['id']}")
                if on:
                    taken_occasional.append(r)

    label = "Update this day" if confirmed else "Took my usual stack"
    if st.button(label, type="primary", width='stretch'):
        rows = [{
            "event_date": d, "category": "supplement", "item": None,
            "detail": {"kind": "day_confirmed",
                       "regimen_ids": [r["id"] for r in daily],
                       "regimen_size": len(daily)},
        }]
        rows += [{
            "event_date": d, "category": "supplement", "item": name,
            "detail": {"kind": "exception", "exception": kind},
        } for name, kind in exceptions.items()]
        rows += [{
            "event_date": d, "category": "supplement", "item": r["name"],
            "quantity": r.get("dose"), "unit": r.get("unit"),
            "detail": {"kind": "taken", "regimen_id": r["id"],
                       "frequency": r.get("frequency")},
        } for r in taken_occasional]
        replace_day("supplement", d, rows)
        n = len(daily) - sum(1 for k in exceptions.values() if k == "skipped")
        flash(f"Confirmed — {n} of {len(daily)} daily taken.")
        st.rerun()

    if confirmed:
        skipped = [k for k, v in prior_exc.items() if v == "skipped"]
        st.success("Day confirmed." + (f" Skipped: {', '.join(skipped)}." if skipped else ""))
    else:
        st.warning("This day is unconfirmed — it will be excluded from adherence.")



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
