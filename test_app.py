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


def open_panel(at, idx):
    at.button[idx].click().run()
    return at


print("home screen")
at = fresh()
check("home renders without exception", not at.exception, at)
check("five panel buttons + settings", len(at.button) >= 6)

print("\nsettings: seed lists, add a supplement regimen")
at = fresh()
at.button[5].click().run()                      # settings
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
wid = sdb.ref_upsert("supplements", {"name": "Probiotic (Vaginal)", "form": "suppository"})
sdb.ref_upsert("supplement_regimens", {
    "supplement_id": wid, "dose": 1.0, "unit": "suppository", "frequency": "1/week",
    "start_date": "2026-02-01", "status": "active"})

at = fresh()
at.button[5].click().run()
check("settings renders the regimen list", not at.exception, at)
check("meal-times tab exposes three editable times", len(at.time_input) == 3)

print("\nalcohol panel")
at = fresh()
open_panel(at, 1)
check("alcohol renders", not at.exception, at)
wine = [b for b in at.button if str(b.label) == "wine"]
check("six drink buttons", len([b for b in at.button if str(b.label) in
                                ("wine", "seltzer", "whiskey", "gin", "beer", "other liquor")]) == 6)
wine[0].click().run()
check("tapping wine adds a line", not at.exception, at)
check("qty defaults to 1", at.number_input[0].value == 1)
check("time-of-day shows all four options",
      list(at.radio[0].options) == ["morning", "afternoon", "evening", "night"])
wine[0].click().run()                            # second wine line
at.number_input[0].set_value(2).run()
at.radio[1].set_value("night").run()
save = [b for b in at.button if "Save" in str(b.label) and "drink" in str(b.label)]
check("save button reflects the running total", len(save) == 1 and "3 drinks" in str(save[0].label))
save[0].click().run()
check("alcohol save succeeds", not at.exception, at)
check("saved rows appear in the day recap",
      any("wine" in str(m.value) for m in at.markdown) or
      any("wine" in str(t.value) for t in at.get("text")), at)

print("\nfood panel")
at = fresh()
open_panel(at, 0)
check("food renders", not at.exception, at)
slots = {str(b.label): b for b in at.button if str(b.label) in C.MEAL_SLOTS}
check("four occasions on screen", len(slots) == 4)
check("exactly one is selected",
      sum(1 for b in at.button if b.proto.type == "primary"
          and str(b.label) in C.MEAL_SLOTS) == 1)
check("a time input is present", len(at.time_input) == 1)

slots["dinner"].click().run()
check("picking dinner prefills its default time",
      at.time_input[0].value.strftime("%H:%M") == C.DEFAULT_MEAL_TIMES["dinner"])
at.time_input[0].set_value(dt_time(22, 15)).run()   # a late dinner
at.text_area[0].set_value("leftover pasta").run()
save = [b for b in at.button if str(b.label) == "Save"]
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
open_panel(at, 2)
check("symptoms renders", not at.exception, at)
bloat = [b for b in at.button if str(b.label) == "bloating"]
check("seeded symptoms are tappable", len(bloat) == 1)
bloat[0].click().run()
check("tapping a symptom adds a line", not at.exception, at)
check("time of day is a multi-select", len(at.pills) == 1)
check("it starts on the current time of day",
      list(at.pills[0].value) == [C.current_bucket()])
at.pills[0].set_value(["morning", "evening"]).run()
sv = [b for b in at.button if "Save" in str(b.label) and "symptom" in str(b.label)]
sv[0].click().run()
check("multi-time symptom saves", not at.exception, at)
sym = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="symptom")
check("one row per time of day", sorted(r["time_bucket"] for r in sym) == ["evening", "morning"])
check("rows share one entry id",
      len({r["detail"]["entry_id"] for r in sym}) == 1)
check("recap shows it as one entry",
      sum(1 for b in at.button if str(b.label) == "✕") == 1)

new_box = [t for t in at.text_input if t.label == "Symptom name"]
check("new-symptom box is on the page", len(new_box) == 1)
new_box[0].set_value("brain fog").run()
[b for b in at.button if str(b.label) == "Add symptom"][0].click().run()
check("adding a new symptom works", not at.exception, at)
check("it joins the symptom list",
      "brain fog" in [t["name"] for t in SQLiteDB(os.environ["TRACKER_DB"]).ref_list("symptom_types")])
check("and opens a line for it straight away",
      any("brain fog" in str(m.value) for m in at.markdown))

print("\nsupplements panel")
at = fresh()
open_panel(at, 3)
check("supplements renders", not at.exception, at)
check("daily supplement is shown as taken by default",
      any(t.label == "Vitamin D3 + K2" and t.value for t in at.toggle))
check("weekly supplement is shown as not taken by default",
      any(t.label == "Probiotic (Vaginal)" and not t.value for t in at.toggle))
save = [b for b in at.button if str(b.label) == "Save changes"]
check("nothing to save when nothing changed", save and save[0].disabled)
check("and no rows are written for an untouched day",
      SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="supplement") == [])

[t for t in at.toggle if t.label == "Vitamin D3 + K2"][0].set_value(False).run()
save = [b for b in at.button if str(b.label) == "Save changes"]
check("turning one off enables save", save and not save[0].disabled)
save[0].click().run()
check("saving a skip works", not at.exception, at)
sup = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="supplement")
check("exactly one row: the skip",
      [(r["item"], r["detail"]["kind"]) for r in sup] == [("Vitamin D3 + K2", "skipped")])

at = fresh()
open_panel(at, 3)
check("the skip is remembered when reopening the day",
      any(t.label == "Vitamin D3 + K2" and not t.value for t in at.toggle))
[t for t in at.toggle if t.label == "Vitamin D3 + K2"][0].set_value(True).run()
[n for n in at.number_input if n.label == "softgel"][0].set_value(2.0).run()
[b for b in at.button if str(b.label) == "Save changes"][0].click().run()
sup = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="supplement")
check("re-saving replaces the skip with a dose change",
      [(r["detail"]["kind"], r["quantity"]) for r in sup] == [("dose_changed", 2.0)])

at = fresh()
open_panel(at, 3)
form_names = [t for t in at.text_input if t.label == "Name (if new)"]
check("add-supplement form is on the page", len(form_names) == 1)
form_names[0].set_value("Vitamin C")
[n for n in at.number_input if n.label == "Dose"][0].set_value(500.0)
[t for t in at.text_input if t.label == "Unit"][0].set_value("mg")
[b for b in at.button if str(b.label) == "Add"][0].click().run()
check("one-off add works", not at.exception, at)
sup = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="supplement")
check("one-off is logged as an extra for the day",
      any(r["item"] == "Vitamin C" and r["detail"]["kind"] == "extra" for r in sup))
check("extras survive alongside saved changes",
      len(SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="supplement")) == 2)

form_names = [t for t in at.text_input if t.label == "Name (if new)"]
form_names[0].set_value("Iron")
[c for c in at.checkbox if c.label == "Part of my usual intake"][0].check()
[b for b in at.button if str(b.label) == "Add"][0].click().run()
check("adding to the usual list works", not at.exception, at)
check("new usual supplement appears in the taken-today list",
      any(t.label == "Iron" and t.value for t in at.toggle))

print("\nhabits panel")
at = fresh()
open_panel(at, 4)
check("habits renders", not at.exception, at)
check("binary habit is a toggle", len(at.toggle) >= 1)
check("count habit is a stepper", len(at.number_input) >= 1)
if at.number_input:
    at.number_input[0].set_value(8).run()
sv = [b for b in at.button if str(b.label) == "Save day"]
check("save-day button present", len(sv) == 1)
if sv:
    sv[0].click().run()
    check("habits save succeeds", not at.exception, at)

print("\ndate navigation + export")
at = fresh()
open_panel(at, 1)
back = [b for b in at.button if str(b.label) == "◀"]
check("previous-day button present", len(back) == 1)
if back:
    back[0].click().run()
    check("stepping back a day works", not at.exception, at)

at = fresh()
at.button[5].click().run()
check("export tab lists events", any("events logged" in str(m.value) for m in at.markdown), at)

print("\npasscode gate")
os.environ["APP_PASSCODE"] = "correct horse battery"
at = fresh()
check("locked app shows only the passcode box",
      not at.exception and len(at.text_input) == 1 and not any("Alcohol" in str(b.label) for b in at.button))
at.text_input[0].set_value("wrong").run()
at.button[0].click().run()
check("a wrong passcode is rejected", len(at.error) == 1)
at = fresh()
at.text_input[0].set_value("correct horse battery").run()
at.button[0].click().run()
check("the right passcode unlocks it", any("Alcohol" in str(b.label) for b in at.button))
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
