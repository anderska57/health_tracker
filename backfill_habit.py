"""Fill in a yes/no habit for a run of past days.

For when you added a habit but forgot to log it. Shows what it would do first;
nothing is written until you add --apply.

  python3 backfill_habit.py                       # list your habits and when each was added
  python3 backfill_habit.py "stretch"             # preview: done every day since it was added
  python3 backfill_habit.py "stretch" --off 2026-10-23 2026-10-24
  python3 backfill_habit.py "stretch" --off 2026-10-23 2026-10-24 --apply

Options:
  --from YYYY-MM-DD   first day to fill (default: the day the habit was added)
  --to YYYY-MM-DD     last day to fill (default: yesterday)
  --off DAY [DAY ...] days to mark as NOT done; every other day is marked done
  --apply             actually write the changes

Each day in the range ends up with exactly one row for the habit (1 = done,
0 = not done), replacing whatever that day had for it. Other habits and other
days are never touched. Uses the same database as the app (Supabase if
.streamlit/secrets.toml has the keys, otherwise tracker.db).
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from db import get_db


def added_on(created_at) -> date | None:
    if not created_at:
        return None
    ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    return (ts.astimezone() if ts.tzinfo else ts).date()    # this computer's time zone


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("habit", nargs="?")
    ap.add_argument("--from", dest="start", type=date.fromisoformat)
    ap.add_argument("--to", dest="end", type=date.fromisoformat)
    ap.add_argument("--off", nargs="*", type=date.fromisoformat, default=[])
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = get_db()
    habits = db.ref_list("habits", active_only=False)
    print(f"storage: {db.kind}\n")

    if not args.habit:
        print("Your habits:")
        for h in habits:
            a = added_on(h.get("created_at"))
            print(f"  {h['name']!r:34} {h['kind']:7} added {a or '?'}")
        return 0

    match = [h for h in habits if h["name"].lower() == args.habit.strip().lower()]
    if not match:
        close = [h["name"] for h in habits if args.habit.lower() in h["name"].lower()]
        print(f"No habit called {args.habit!r}." + (f" Did you mean: {close}?" if close else ""))
        return 1
    h = match[0]
    if h["kind"] != "binary":
        print(f"{h['name']} is a count habit; this script only fills yes/no habits.")
        return 1

    start = args.start or added_on(h.get("created_at"))
    end = args.end or (date.today() - timedelta(days=1))
    if not start or end < start:
        print(f"Nothing to fill between {start} and {end}.")
        return 1
    off = set(args.off)
    stray = sorted(d for d in off if not start <= d <= end)
    if stray:
        print(f"Note: {', '.join(map(str, stray))} is outside {start} .. {end} and is ignored.")

    print(f"{h['name']} was added {added_on(h.get('created_at'))}. Filling {start} .. {end}:\n")
    existing: dict[str, list[dict]] = {}
    for e in db.events_fetch(start=start, end=end, category="habit", limit=100000):
        if e.get("item") == h["name"]:
            existing.setdefault(str(e["event_date"])[:10], []).append(e)

    plan, day = [], start
    while day <= end:
        want = 0 if day in off else 1
        had = existing.get(day.isoformat(), [])
        was = ", ".join(f"{float(e.get('quantity') or 0):g}" for e in had) or "blank"
        print(f"  {day:%a %b %d}   was {was:6}  ->  {'done' if want else 'NOT done'}")
        plan.append((day, want, had))
        day += timedelta(days=1)

    if not args.apply:
        print("\nPreview only. Add --apply to the end of the same command to write it.")
        return 0

    for day, want, had in plan:
        for e in had:
            db.event_delete(e["id"])
    db.events_insert([{
        "event_date": day, "category": "habit", "item": h["name"], "quantity": want,
        "unit": h.get("unit") or "done", "detail": {"kind": "binary", "backfilled": True}}
        for day, want, _ in plan])
    print(f"\nWrote {len(plan)} days for {h['name']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
