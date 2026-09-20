"""Smoke test: exercise every write path against a throwaway SQLite file."""

import os
import tempfile
from datetime import date, datetime

import config as C
from db import SQLiteDB

tmp = os.path.join(tempfile.mkdtemp(), "t.db")
db = SQLiteDB(tmp)
d = date.today()
fails = []


def check(label, cond):
    print(("  ok  " if cond else "  FAIL") + f"  {label}")
    if not cond:
        fails.append(label)


print("time buckets")
for h, want in [(7, "morning"), (13, "afternoon"), (19, "evening"),
                (23, "night"), (2, "night"), (5, "morning")]:
    check(f"{h:02d}:00 -> {want}",
          C.current_bucket(datetime(2026, 1, 1, h)) == want)

print("\nreference tables: catalog + regimens")
sid = db.ref_upsert("supplements", {"name": "Vitamin D3 + K2", "brand": "Micro Ingredients",
                                    "form": "softgel", "sort_order": 0})
mid = db.ref_upsert("supplements", {"name": "Magnesium (Glycinate)", "form": "capsule",
                                    "sort_order": 1})
db.ref_upsert("supplement_regimens", {
    "supplement_id": sid, "dose": 1.0, "unit": "softgel", "frequency": "1/day",
    "start_date": "2026-01-01", "status": "active", "notes": "5000 IU D3 with K2"})
db.ref_upsert("supplement_regimens", {
    "supplement_id": mid, "dose": 1.0, "unit": "capsule", "frequency": "1/day",
    "start_date": "2021-06-01", "end_date": "2025-12-31", "status": "stopped"})
db.ref_upsert("symptom_types", {"name": "bloating", "active": True})
db.ref_upsert("habits", {"name": "exercise", "kind": "binary", "active": True})
db.ref_upsert("habits", {"name": "water", "kind": "count", "unit": "glasses", "active": True})
check("2 supplements in catalog", len(db.ref_list("supplements")) == 2)
check("2 regimens", len(db.ref_list("supplement_regimens")) == 2)
check("1 symptom type", len(db.ref_list("symptom_types")) == 1)
check("2 habits", len(db.ref_list("habits")) == 2)

print("\nregimens are date-aware")
act = db.regimens_on(date(2026, 6, 1))
check("today shows only the regimen running today",
      [r["name"] for r in act] == ["Vitamin D3 + K2"])
check("regimen rows carry catalog fields", act[0]["brand"] == "Micro Ingredients")
past = db.regimens_on(date(2023, 6, 1))
check("a 2023 date shows what was being taken in 2023 (now stopped)",
      [r["name"] for r in past] == ["Magnesium (Glycinate)"])
check("a date before either regimen started shows nothing",
      db.regimens_on(date(2019, 1, 1)) == [])
check("the last day of a stopped regimen still counts as inside it",
      [r["name"] for r in db.regimens_on(date(2025, 12, 31))] == ["Magnesium (Glycinate)"])
db.ref_upsert("supplement_regimens", {
    "supplement_id": mid, "dose": 1.0, "unit": "capsule", "frequency": "1/day",
    "start_date": "2026-03-01", "status": "paused"})
check("a paused regimen is excluded from its own span",
      "Magnesium (Glycinate)" not in [r["name"] for r in db.regimens_on(date(2026, 6, 1))])

db.ref_upsert("supplements", {"id": sid, "name": "Vitamin D3 + K2",
                              "brand": "Micro Ingredients", "form": "softgel"})
check("catalog upsert does not duplicate", len(db.ref_list("supplements")) == 2)

print("\ndaily vs occasional frequency")
for f, want in [("1/day", True), ("2/day", True), ("daily", True),
                ("1/week", False), ("3/week", False), ("as needed", False)]:
    check(f"{f!r} daily={want}", C.is_daily(f) is want)

print("\nalcohol: two wine lines at different times + a beer")
db.events_insert([
    {"event_date": d, "time_bucket": "evening", "category": "alcohol",
     "item": "wine", "quantity": 2, "unit": "drinks"},
    {"event_date": d, "time_bucket": "night", "category": "alcohol",
     "item": "wine", "quantity": 1, "unit": "drinks"},
    {"event_date": d, "time_bucket": "evening", "category": "alcohol",
     "item": "beer", "quantity": 1, "unit": "drinks"},
])
alc = db.events_fetch(start=d, end=d, category="alcohol")
check("3 rows", len(alc) == 3)
check("4 drinks total", sum(r["quantity"] for r in alc) == 4)
check("same drink type at two buckets is distinguishable",
      {r["time_bucket"] for r in alc if r["item"] == "wine"} == {"evening", "night"})
check("night total recoverable",
      sum(r["quantity"] for r in alc if r["time_bucket"] == "night") == 1)

print("\nfood: free text, occasion, and a real clock time")
db.events_insert([
    {"event_date": d, "event_time": "08:30", "time_bucket": "morning",
     "category": "food", "item": "oatmeal, blueberries, coffee",
     "detail": {"slot": "breakfast"}},
    {"event_date": d, "event_time": "22:15", "time_bucket": "night",
     "category": "food", "item": "leftover pasta", "detail": {"slot": "dinner"}},
])
food = {r["detail"]["slot"]: r for r in db.events_fetch(start=d, end=d, category="food")}
check("2 rows", len(food) == 2)
check("occasion round-trips", set(food) == {"breakfast", "dinner"})
check("clock time stored", food["breakfast"]["event_time"] == "08:30")
check("a late dinner is still dinner", food["dinner"]["detail"]["slot"] == "dinner")
check("...but its time bucket is night", food["dinner"]["time_bucket"] == "night")

print("\nsettings round-trip")
check("missing key returns the fallback",
      db.setting_get("meal_time_breakfast", "08:30") == "08:30")
db.setting_set("meal_time_breakfast", "07:15")
check("stored value comes back", db.setting_get("meal_time_breakfast") == "07:15")
db.setting_set("meal_time_breakfast", "07:45")
check("overwrite works", db.setting_get("meal_time_breakfast") == "07:45")

print("\nsymptoms: severity + note")
db.events_insert([{"event_date": d, "time_bucket": "night", "category": "symptom",
                   "item": "bloating", "severity": 4, "note": "after dinner"}])
sym = db.events_fetch(start=d, end=d, category="symptom")
check("severity stored", sym[0]["severity"] == 4)
check("note stored", sym[0]["note"] == "after dinner")

print("\nsupplements: confirm day, then update with a skip")
def replace_day(cat, day, rows):
    for e in db.events_fetch(start=day, end=day, category=cat):
        db.event_delete(e["id"])
    db.events_insert(rows)

replace_day("supplement", d, [
    {"event_date": d, "category": "supplement", "item": None,
     "detail": {"kind": "day_confirmed", "regimen_size": 2}},
])
sup = db.events_fetch(start=d, end=d, category="supplement")
check("day confirmed with no exceptions", len(sup) == 1)

replace_day("supplement", d, [
    {"event_date": d, "category": "supplement", "item": None,
     "detail": {"kind": "day_confirmed", "regimen_size": 2}},
    {"event_date": d, "category": "supplement", "item": "magnesium",
     "detail": {"kind": "exception", "exception": "skipped"}},
])
sup = db.events_fetch(start=d, end=d, category="supplement")
check("re-save replaces rather than duplicates", len(sup) == 2)
check("confirmation still present",
      sum(1 for r in sup if r["detail"].get("kind") == "day_confirmed") == 1)
check("skip recorded",
      any(r["item"] == "magnesium" and r["detail"]["exception"] == "skipped" for r in sup))

print("\nhabits: binary + count, idempotent re-save")
replace_day("habit", d, [
    {"event_date": d, "category": "habit", "item": "exercise", "quantity": 1,
     "unit": "done", "detail": {"kind": "binary"}},
    {"event_date": d, "category": "habit", "item": "water", "quantity": 6,
     "unit": "glasses", "detail": {"kind": "count"}},
])
replace_day("habit", d, [
    {"event_date": d, "category": "habit", "item": "exercise", "quantity": 0,
     "unit": "done", "detail": {"kind": "binary"}},
    {"event_date": d, "category": "habit", "item": "water", "quantity": 8,
     "unit": "glasses", "detail": {"kind": "count"}},
])
hab = {r["item"]: r for r in db.events_fetch(start=d, end=d, category="habit")}
check("still 2 rows after re-save", len(hab) == 2)
check("count updated to 8", hab["water"]["quantity"] == 8)
check("binary flipped to 0", hab["exercise"]["quantity"] == 0)

print("\ncross-category queries")
allrows = db.events_fetch()
check("every category present in one table",
      {"alcohol", "food", "symptom", "supplement", "habit"} <= {r["category"] for r in allrows})
check("date filter excludes other days",
      db.events_fetch(start=date(2020, 1, 1), end=date(2020, 1, 2)) == [])
check("logged_at is populated on every row", all(r["logged_at"] for r in allrows))

print("\ndelete")
before = len(db.events_fetch(category="food"))
db.event_delete(db.events_fetch(category="food")[0]["id"])
check("row gone", len(db.events_fetch(category="food")) == before - 1)

print("\nCSV export shape")
import pandas as pd
df = pd.DataFrame(db.events_fetch(limit=100000))
check("dataframe builds", len(df) > 0)
check("expected columns",
      {"event_date", "event_time", "time_bucket", "category", "item", "quantity",
       "severity", "note", "detail", "logged_at"} <= set(df.columns))


print("\nmeal occasions")
check("four occasions", C.MEAL_SLOTS == ["breakfast", "lunch", "dinner", "snack"])
check("three have configurable defaults",
      set(C.DEFAULT_MEAL_TIMES) == {"breakfast", "lunch", "dinner"})
check("defaults match what was asked for",
      C.DEFAULT_MEAL_TIMES == {"breakfast": "08:30", "lunch": "12:00", "dinner": "18:00"})
for t, want in [("08:30", "morning"), ("12:00", "afternoon"),
                ("18:00", "evening"), ("22:15", "night")]:
    hh, mm = (int(x) for x in t.split(":"))
    check(f"{t} falls in {want}",
          C.current_bucket(datetime(2026, 1, 1, hh, mm)) == want)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    raise SystemExit(1)
print("all checks passed")
