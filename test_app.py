"""Render every panel headlessly with Streamlit's AppTest and click through
the real save paths, so a typo or a misused widget API fails here, not on a phone."""

import os
import tempfile

os.environ["TRACKER_DB"] = os.path.join(tempfile.mkdtemp(), "apptest.db")

from datetime import time as dt_time  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

fails = []


def check(label, cond, at=None):
    ok = bool(cond) and not (at is not None and at.exception)
    print(("  ok  " if ok else "  FAIL") + f"  {label}")
    if not ok:
        if at is not None and at.exception:
            print("        " + str(at.exception[0].value)[:400])
        fails.append(label)


def fresh():
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()
    return at


def open_panel(at, cat):
    at.button(key=f"panel_{cat}").click().run()
    return at


def btn(at, label):
    return [b for b in at.button if str(b.label) == label]


print("home screen")
at = fresh()
check("home renders without exception", not at.exception, at)
check("six category tiles", all(at.button(key=f"panel_{c}") for c in
                                ("food", "alcohol", "symptom", "supplement", "medication", "habit")))
check("settings gear present", at.button(key="settings") is not None)

print("\nsettings: seed lists, add regimens")
at = fresh()
at.button(key="settings").click().run()
check("settings renders", not at.exception, at)
seed = [b for b in at.button if "Seed" in str(b.label)]
check("seed button present", len(seed) == 1)
seed[0].click().run()
check("seeding works", not at.exception, at)

import config as C  # noqa: E402
from db import SQLiteDB  # noqa: E402
sdb = SQLiteDB(os.environ["TRACKER_DB"])
sid = sdb.ref_upsert("supplements", {"name": "Vitamin D3 + K2", "form": "softgel"})
sdb.ref_upsert("supplement_regimens", {
    "supplement_id": sid, "dose": 1.0, "unit": "softgel", "frequency": "1/day",
    "start_date": "2026-01-01", "status": "active"})
wid = sdb.ref_upsert("supplements", {"name": "Iron", "form": "tablet"})
sdb.ref_upsert("supplement_regimens", {
    "supplement_id": wid, "dose": 1.0, "unit": "tablet", "frequency": "1/week",
    "start_date": "2026-02-01", "status": "active"})
mid = sdb.ref_upsert("supplements", {"name": "Allergy tablet", "kind": "medication"})
sdb.ref_upsert("supplement_regimens", {
    "supplement_id": mid, "dose": 10.0, "unit": "mg", "frequency": "1/day",
    "start_date": "2026-01-01", "status": "active"})
pid = sdb.ref_upsert("supplements", {"name": "Pain reliever", "kind": "medication"})
sdb.ref_upsert("supplement_regimens", {
    "supplement_id": pid, "dose": 200.0, "unit": "mg", "frequency": "as needed",
    "start_date": "2026-01-01", "status": "active"})

at = fresh()
at.button(key="settings").click().run()
check("settings renders the regimen lists", not at.exception, at)
check("meal-times tab exposes three editable times", len(at.time_input) == 3)
check("settings has a Medications tab", any(t.label == "Medications" for t in at.tabs))

print("\nalcohol panel")
at = fresh()
open_panel(at, "alcohol")
check("alcohol renders", not at.exception, at)
check("six drink buttons", len([b for b in at.button if str(b.label) in C.DRINK_TYPES]) == 6)
btn(at, "wine")[0].click().run()
check("tapping wine adds a line", not at.exception, at)
check("qty defaults to 1", at.number_input[0].value == 1)
check("time of day is a compact picker with all four options",
      list(at.selectbox[0].options) == C.TIME_BUCKETS)
check("it starts on the current time of day", at.selectbox[0].value == C.current_bucket())
btn(at, "wine")[0].click().run()                 # second wine line
at.number_input[0].set_value(2).run()
at.selectbox[1].set_value("night").run()
save = [b for b in at.button if "Save" in str(b.label) and "drink" in str(b.label)]
check("save button reflects the running total", len(save) == 1 and "3 drinks" in str(save[0].label))
save[0].click().run()
check("alcohol save succeeds", not at.exception, at)
alc = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="alcohol")
check("two alcohol rows saved", sorted(int(r["quantity"]) for r in alc) == [1, 2])
check("saved rows appear in the day recap", any("wine" in str(m.value) for m in at.markdown))
check("the recap is headed 'Logged this day' with 'delete entry' over the ✕s",
      any("Logged this day" in str(m.value) for m in at.markdown)
      and any("delete entry" in str(m.value) for m in at.markdown))

print("\nfood panel")
at = fresh()
open_panel(at, "food")
check("food renders", not at.exception, at)
check("four occasions on screen", len([b for b in at.button if str(b.label) in C.MEAL_SLOTS]) == 4)
check("exactly one is selected",
      sum(1 for b in at.button if b.proto.type == "primary"
          and str(b.label) in C.MEAL_SLOTS) == 1)
check("a time input is present", len(at.time_input) == 1)
import inspect as _i  # noqa: E402
if "format" in _i.signature(__import__("streamlit").time_input).parameters:
    check("times show as 12-hour with am/pm", "12" in str(at.time_input[0].proto.format) or
          at.time_input[0].proto.format == 1)
btn(at, "dinner")[0].click().run()
check("picking dinner prefills its default time",
      at.time_input[0].value.strftime("%H:%M") == C.DEFAULT_MEAL_TIMES["dinner"])
at.time_input[0].set_value(dt_time(22, 15)).run()   # a late dinner
at.text_area[0].set_value("leftover pasta").run()
save = btn(at, "Save")
check("save button present", len(save) == 1)
save[0].click().run()
check("food save succeeds", not at.exception, at)
rows = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="food")
check("one food row saved", len(rows) == 1)
if rows:
    check("occasion stored", rows[0]["detail"]["slot"] == "dinner")
    check("clock time stored", rows[0]["event_time"] == "22:15")
    check("a 10pm dinner is bucketed as night", rows[0]["time_bucket"] == "night")

print("\nsymptoms panel")
at = fresh()
open_panel(at, "symptom")
check("symptoms renders", not at.exception, at)
pick = at.pills[0]
check("symptoms are an alphabetical picker",
      list(pick.options) == sorted(C.DEFAULT_SYMPTOMS, key=str.lower))
check("New symptom sits in the top bar, not among the symptoms",
      at.button(key="add_btn") is not None and "New" in str(at.button(key="add_btn").label)
      and not any("New" in o for o in pick.options))
check("Save starts disabled", at.button(key="save_btn").disabled)
pick.set_value(["bloating"]).run()
check("picking a symptom adds a line", not at.exception and len(at.pills) == 2, at)
check("time of day is a multi-select starting now", list(at.pills[1].value) == [C.current_bucket()])
check("severity is a compact picker defaulting to 3", at.selectbox[0].value == 3)
at.pills[1].set_value(["morning", "evening"]).run()
check("save button counts the symptoms", str(at.button(key="save_btn").label) == "Save 1 symptom")
at.button(key="save_btn").click().run()
check("multi-time symptom saves", not at.exception, at)
sym = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="symptom")
check("one row per time of day", sorted(r["time_bucket"] for r in sym) == ["evening", "morning"])
check("rows share one entry id", len({r["detail"]["entry_id"] for r in sym}) == 1)
check("recap shows it as one entry", sum(1 for b in at.button if str(b.label) == "✕") == 1)
check("the picker is cleared after saving", not at.pills[0].value)

at.button(key="add_btn").click().run()
box = [t for t in at.text_input if t.label == "Symptom name"]
check("new-symptom dialog opens", len(box) == 1)
box[0].set_value("Brain fog")
btn(at, "Add symptom")[0].click().run()
check("adding a new symptom works", not at.exception, at)
check("it joins the symptom list",
      "Brain fog" in [t["name"] for t in SQLiteDB(os.environ["TRACKER_DB"]).ref_list("symptom_types")])
check("the dialog closes and a line opens for it",
      not [t for t in at.text_input if t.label == "Symptom name"]
      and any("Brain fog" in str(m.value) for m in at.markdown))
check("and it is selected, in alphabetical place, in the picker",
      list(at.pills[0].value) == ["Brain fog"] and list(at.pills[0].options)[1] == "Brain fog")
at.button(key="sym_sx_" + [k for k in [b.key for b in at.button] if k and k.startswith("sym_sx_")][0][7:]).click().run()
check("✕ on a pending line drops it and unselects it", not at.pills[0].value and len(at.pills) == 1)


def regimen_checks(kind, daily_name, daily_unit, occasional_name):
    title = {"supplement": "supplements", "medication": "medications"}[kind]
    print(f"\n{title} panel")
    at = fresh()
    open_panel(at, kind)
    check(f"{title} renders", not at.exception, at)
    check("daily item is checked by default",
          any(c.label == daily_name and c.value for c in at.checkbox))
    check("weekly / as-needed item is unchecked by default",
          any(c.label == occasional_name and not c.value for c in at.checkbox))
    check("only this panel's items are listed",
          not any(c.label in ("Allergy tablet", "Vitamin D3 + K2") and c.label != daily_name
                  for c in at.checkbox))
    save = at.button(key="save_btn")
    check("save and add sit side by side above the list",
          [b.key for b in at.button][3:5] == ["save_btn", "add_btn"])
    check("nothing to save when nothing changed", save.disabled)
    check("and no rows are written for an untouched day",
          SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category=kind) == [])

    [c for c in at.checkbox if c.label == daily_name][0].uncheck().run()
    check("unchecking one enables save", not at.button(key="save_btn").disabled)
    at.button(key="save_btn").click().run()
    check("saving a skip works", not at.exception, at)
    ev = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category=kind)
    check("exactly one row: the skip",
          [(r["item"], r["detail"]["kind"]) for r in ev] == [(daily_name, "skipped")])

    at = fresh()
    open_panel(at, kind)
    check("the skip is remembered when reopening the day",
          any(c.label == daily_name and not c.value for c in at.checkbox))
    [c for c in at.checkbox if c.label == daily_name][0].check().run()
    [n for n in at.number_input if n.label == f"{daily_name} dose"][0].set_value(2.0).run()
    at.button(key="save_btn").click().run()
    ev = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category=kind)
    check("re-saving replaces the skip with a dose change",
          [(r["detail"]["kind"], r["quantity"]) for r in ev] == [("dose_changed", 2.0)])

    [c for c in at.checkbox if c.label == occasional_name][0].check().run()
    at.button(key="save_btn").click().run()
    ev = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category=kind)
    check("checking an occasional item logs it as taken",
          any(r["item"] == occasional_name and r["detail"]["kind"] == "taken" for r in ev))

    at = fresh()
    open_panel(at, kind)
    at.button(key="add_btn").click().run()
    p = f"add_{kind}"
    check("add dialog opens", at.text_input(key=f"{p}_name") is not None, at)
    at.text_input(key=f"{p}_name").set_value(f"One-off {kind}")
    at.number_input(key=f"{p}_dose").set_value(500.0)
    at.text_input(key=f"{p}_unit").set_value("mg")
    at.button(key=f"{p}_go").click().run()
    check("one-off add works", not at.exception, at)
    ev = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category=kind)
    check("one-off is logged as an extra for the day",
          any(r["item"] == f"One-off {kind}" and r["detail"]["kind"] == "extra" for r in ev))
    check("the dialog closes after adding", not [t for t in at.text_input if t.key == f"{p}_name"])
    check("extras survive alongside saved changes", len(ev) == 3)

    at.button(key="add_btn").click().run()
    at.text_input(key=f"{p}_name").set_value(f"New usual {kind}")
    at.checkbox(key=f"{p}_usual").check().run()
    check("ticking usual reveals how-often", at.selectbox(key=f"{p}_freq") is not None)
    at.button(key=f"{p}_go").click().run()
    check("adding to the usual list works", not at.exception, at)
    check("new usual item appears checked in the daily list",
          any(c.label == f"New usual {kind}" and c.value for c in at.checkbox))
    cat = SQLiteDB(os.environ["TRACKER_DB"]).ref_list("supplements")
    check(f"it is stored as a {kind}",
          any(s["name"] == f"New usual {kind}" and s["kind"] == kind for s in cat))


regimen_checks("supplement", "Vitamin D3 + K2", "softgel", "Iron")
regimen_checks("medication", "Allergy tablet", "mg", "Pain reliever")

at = fresh()
open_panel(at, "supplement")
at.button(key="add_btn").click().run()
at.selectbox(key="add_supplement_pick").set_value("— new —")
at.text_input(key="add_supplement_name").set_value("allergy TABLET")
at.button(key="add_supplement_go").click().run()
check("a medication name can't be re-added as a supplement",
      any("already on your list as a medication" in str(w.value) for w in at.warning))

print("\nmedication taken for a symptom")
at = fresh()
open_panel(at, "medication")
[c for c in at.checkbox if c.label == "Pain reliever"][0].uncheck().run()   # reset from earlier
if not at.button(key="save_btn").disabled:
    at.button(key="save_btn").click().run()
check("as-needed medication has no 'why' until checked", len(at.pills) == 0)
[c for c in at.checkbox if c.label == "Pain reliever"][0].check().run()
check("checking it asks why", len(at.pills) == 1 and
      list(at.pills[0].options) == ["preventive", "for a symptom"])
at.pills[0].set_value("for a symptom").run()
at.button(key="save_btn").click().run()
check("saving opens the symptom follow-up", not at.exception and
      any("for a symptom" in str(m.value) and "Pain reliever" in str(m.value) for m in at.markdown), at)
ev = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="medication")
check("the reason is stored with the medication",
      any(r["item"] == "Pain reliever" and r["detail"].get("purpose") == "symptom" for r in ev))
mp = [p for p in at.pills if p.key and p.key.startswith("medsym_pick_")][0]
check("the follow-up uses the same alphabetical symptom list",
      list(mp.options) == sorted(mp.options, key=str.lower) and "headache" in mp.options)
mp.set_value(["headache"]).run()
check("picking a symptom there opens the same questions",
      any(s.key and s.key.startswith("medsym_sv_") for s in at.selectbox))
at.button(key="medsym_save").click().run()
check("saving the follow-up works", not at.exception, at)
sym = [r for r in SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="symptom")
       if r["item"] == "headache"]
check("the symptom is logged, linked to the medication",
      sym and sym[0]["detail"].get("treated_with") == ["Pain reliever"])
check("the pop-up closes", not [b for b in at.button if b.key == "medsym_save"])
at.run()
check("re-saving without changes doesn't ask again",
      at.button(key="save_btn").disabled and not [b for b in at.button if b.key == "medsym_save"])

at.button(key="add_btn").click().run()
at.text_input(key="add_medication_name").set_value("Antacid")
check("a one-off medication asks why", at.pills(key="add_medication_purpose") is not None)
at.pills(key="add_medication_purpose").set_value("for a symptom").run()
at.button(key="add_medication_go").click().run()
check("adding it for a symptom goes straight to the follow-up",
      [b for b in at.button if b.key == "medsym_skip"] and not at.exception, at)
at.button(key="medsym_skip").click().run()
check("Skip closes it without logging a symptom",
      not [b for b in at.button if b.key == "medsym_skip"]
      and len([r for r in SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="symptom")
               if r["detail"].get("treated_with") == ["Antacid"]]) == 0)
ev = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="medication")
check("the one-off keeps its reason",
      any(r["item"] == "Antacid" and r["detail"].get("purpose") == "symptom" for r in ev))

at = fresh()
open_panel(at, "supplement")
check("supplements never ask why", len(at.pills) == 0 or
      not any("for a symptom" in (p.options or []) for p in at.pills))

print("\nhome tiles reflect the day")
at = fresh()
labels = {b.key: str(b.label) for b in at.button}
check("alcohol tile shows today's drinks", "3 drinks" in labels["panel_alcohol"])
check("supplement tile shows the day was edited", "edited" in labels["panel_supplement"])

print("\nhabits panel")
at = fresh()
open_panel(at, "habit")
check("habits renders", not at.exception, at)
check("binary habit is a checkbox", len(at.checkbox) >= 1)
check("count habit is a number box", len(at.number_input) >= 1)
if at.number_input:
    at.number_input[0].set_value(8).run()
sv = at.button(key="save_btn")
check("save-day button present above the list", str(sv.label) == "Save day")
sv.click().run()
check("habits save succeeds", not at.exception, at)
check("button then reads Update day", str(at.button(key="save_btn").label) == "Update day")

print("\ndate navigation + export")
at = fresh()
open_panel(at, "alcohol")
at.button(key="prev_day").click().run()
check("stepping back a day works", not at.exception, at)
check("the date line says Yesterday",
      any("Yesterday" in str(p.proto.popover.label) for p in at.get("popover")))
check("forward is enabled again", not at.button(key="next_day").disabled)
at.button(key="back").click().run()
check("back arrow returns home", at.button(key="panel_food") is not None and not at.exception)

at = fresh()
at.button(key="settings").click().run()
check("export tab lists events", any("events logged" in str(m.value) for m in at.markdown), at)

print("\npasscode gate")
os.environ["APP_PASSCODE"] = "correct horse battery"
at = fresh()
check("locked app shows only the passcode box",
      not at.exception and len(at.text_input) == 1 and not any("Alcohol" in str(b.label) for b in at.button))
at.text_input[0].set_value("wrong")          # typed and submitted together,
at.button[0].click().run()                   # as a browser sends a form
check("a wrong passcode is rejected", len(at.error) == 1)
at = fresh()
at.text_input[0].set_value("correct horse battery")
at.button[0].click().run()
check("the right passcode unlocks it", any("Alcohol" in str(b.label) for b in at.button))
check("remember-this-device is on by default",
      any(c.label == "Remember this device" for c in AppTest.from_file("app.py").run().checkbox
          if c.value))
lock = [b for b in at.button if "Lock this device" in str(b.label)]
check("a Lock this device button is offered once unlocked", len(lock) == 1)
lock[0].click().run()
at.run()   # let the screen settle (older AppTest keeps the pre-rerun tree)
check("locking returns to the passcode screen",
      not any("Alcohol" in str(b.label) for b in at.button) and len(at.text_input) == 1)
at = AppTest.from_file("app.py", default_timeout=30)
at.query_params["k"] = "correct horse battery"
at.run()
check("a passcode in the URL opens straight in",
      not at.exception and any("Alcohol" in str(b.label) for b in at.button))
at = AppTest.from_file("app.py", default_timeout=30)
at.query_params["k"] = "nope"
at.run()
check("a wrong URL passcode stays locked", not any("Alcohol" in str(b.label) for b in at.button))
del os.environ["APP_PASSCODE"]

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    raise SystemExit(1)
print("all checks passed")
