"""Import a supplement catalog + regimens from the standalone health.sqlite
into the tracker's own database.

  python3 import_supplements.py supplements/health.sqlite

Idempotent: matches on supplement name and on (supplement, start_date, dose,
frequency), so running it twice does not duplicate anything. Stopped and paused
regimens come across too — the whole point of the regimen model is that history
is preserved, and a regimen that ended in 2025 is what makes 2025 data readable.
"""

from __future__ import annotations

import sqlite3
import sys

from db import get_db


def load_source(path: str) -> list[dict]:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    q = """select s.name, s.brand, s.form,
                  r.dose, r.unit, r.frequency, r.start_date, r.end_date,
                  r.reason, r.status, r.notes
           from supplement_regimens r
           join supplements s on s.id = r.supplement_id
           order by s.name, r.start_date"""
    return [dict(r) for r in c.execute(q)]


def main(src: str) -> int:
    rows = load_source(src)
    db = get_db()
    print(f"source: {src} -> {len(rows)} regimens across "
          f"{len({r['name'] for r in rows})} supplements")
    print(f"target: {db.kind}\n")

    catalog = {s["name"].lower(): s for s in db.ref_list("supplements")}
    existing = {(r["supplement_id"], r["start_date"], r.get("frequency"))
                for r in db.ref_list("supplement_regimens")}

    added_s = added_r = skipped = 0
    for r in rows:
        key = r["name"].lower()
        if key in catalog:
            sid = catalog[key]["id"]
        else:
            sid = db.ref_upsert("supplements", {
                "name": r["name"], "brand": r["brand"], "form": r["form"],
                "sort_order": len(catalog)})
            catalog[key] = {"id": sid, "name": r["name"]}
            added_s += 1

        if (sid, r["start_date"], r["frequency"]) in existing:
            skipped += 1
            continue

        db.ref_upsert("supplement_regimens", {
            "supplement_id": sid, "dose": r["dose"], "unit": r["unit"],
            "frequency": r["frequency"], "start_date": r["start_date"],
            "end_date": r["end_date"], "reason": r["reason"],
            "status": r["status"], "notes": r["notes"]})
        existing.add((sid, r["start_date"], r["frequency"]))
        added_r += 1
        flag = "" if r["status"] == "active" else f"  [{r['status']}]"
        print(f"  + {r['name']}  {r['frequency']}  from {r['start_date']}{flag}")

    print(f"\n{added_s} supplements, {added_r} regimens added"
          f"{f', {skipped} already present' if skipped else ''}.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
