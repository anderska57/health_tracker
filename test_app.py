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
if bloat:
    bloat[0].click().run()
    check("tapping a symptom adds a line", not at.exception, at)
    sv = [b for b in at.button if "Save" in str(b.label) and "symptom" in str(b.label)]
    check("symptom save button present", len(sv) == 1)
    if sv:
        sv[0].click().run()
        check("symptom save succeeds", not at.exception, at)

print("\nsupplements panel")
at = fresh()
open_panel(at, 3)
check("supplements renders", not at.exception, at)
conf = [b for b in at.button if "usual stack" in str(b.label) or "Update this day" in str(b.label)]
check("confirm button present", len(conf) == 1)
check("the daily regimen gets a took-it/skipped/extra choice", len(at.radio) == 1)
check("the weekly regimen gets its own opt-in toggle", len(at.toggle) == 1)
if conf:
    conf[0].click().run()
    check("confirming the day succeeds", not at.exception, at)
    check("confirmed state is shown", len(at.success) >= 1, at)
    sup = SQLiteDB(os.environ["TRACKER_DB"]).events_fetch(category="supplement")
    conf_rows = [r for r in sup if r["detail"].get("kind") == "day_confirmed"]
    check("exactly one confirmation row", len(conf_rows) == 1)
    check("confirmation records which daily regimens were expected",
          conf_rows and conf_rows[0]["detail"]["regimen_size"] == 1)
    check("the untapped weekly regimen wrote no row",
          not [r for r in sup if r["detail"].get("kind") == "taken"])

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

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    raise SystemExit(1)
print("all checks passed")
