"""Copy everything in the local tracker.db up to Supabase.

  python3 migrate_to_supabase.py --dry-run    # show what would move, no network
  python3 migrate_to_supabase.py              # actually do it

Reads the SQLite file directly and writes through the Supabase client, so it
needs .streamlit/secrets.toml (or SUPABASE_URL / SUPABASE_KEY in the
environment) to be set up first.

Row ids are carried across unchanged, which keeps supplement_regimens pointing
at the right supplements and makes the whole thing idempotent: every write is
an upsert keyed on id, so running it twice changes nothing.
"""

from __future__ import annotations

import sys

from db import REF_TABLES, SQLiteDB, SupabaseDB, _secret

# supplements before supplement_regimens -- the regimen rows reference them
ORDER = ("supplements", "supplement_regimens", "symptom_types", "habits")


def main(dry_run: bool) -> int:
    src = SQLiteDB("tracker.db")

    plan: dict[str, list[dict]] = {t: src.ref_list(t, active_only=False) for t in ORDER}
    plan["events"] = src.events_fetch(limit=1_000_000)
    with src._conn() as c:
        settings = [dict(r) for r in c.execute("select key, value from app_settings")]
    plan["app_settings"] = settings

    print("Local tracker.db holds:")
    for name, rows in plan.items():
        print(f"  {name:22s} {len(rows)}")
    if plan["events"]:
        dates = [e["event_date"] for e in plan["events"]]
        print(f"\n  events span {min(dates)} .. {max(dates)}")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    if not (_secret("SUPABASE_URL") and _secret("SUPABASE_KEY")):
        print("\nSUPABASE_URL / SUPABASE_KEY not found. Add them to "
              ".streamlit/secrets.toml first.")
        return 1

    dst = SupabaseDB(_secret("SUPABASE_URL"), _secret("SUPABASE_KEY"))
    print("\nWriting to Supabase...")

    for name in ORDER:
        rows = plan[name]
        if rows:
            dst.client.table(name).upsert(rows).execute()
        print(f"  {name:22s} {len(rows)}")

    if settings:
        dst.client.table("app_settings").upsert(settings).execute()
    print(f"  {'app_settings':22s} {len(settings)}")

    events = plan["events"]
    for i in range(0, len(events), 500):          # chunked, in case it grows
        dst.client.table("events").upsert(events[i:i + 500]).execute()
    print(f"  {'events':22s} {len(events)}")

    # read back one count as a sanity check rather than trusting the writes
    back = dst.events_fetch(limit=1_000_000)
    print(f"\nSupabase now reports {len(back)} events "
          f"({'matches' if len(back) == len(events) else 'DOES NOT MATCH'} the local {len(events)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry-run" in sys.argv))
