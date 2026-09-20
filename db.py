"""Storage layer.

Two backends behind one interface:

  * SQLite   -- zero setup, used for local development and testing
  * Supabase -- the real thing, durable and queryable from anywhere

The UI never imports either backend directly. Swapping Streamlit for a
different front end later means rewriting app.py and nothing else; swapping
storage means adding a class here and nothing else.

Backend selection:
  - if st.secrets (or env) carry SUPABASE_URL and SUPABASE_KEY -> Supabase
  - otherwise -> SQLite at ./tracker.db

Supplements follow the catalog/regimen split from health.sqlite: a supplement
is stored once, and a *regimen* is a stretch of time you intended to take it a
certain way. Restarting something after a break is a new regimen row, so
distinct trials stay separable and "what was I on last March?" is answerable.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import date, datetime
from typing import Any

REF_TABLES = ("supplements", "supplement_regimens", "symptom_types", "habits")

_EVENT_FIELDS = (
    "id", "event_date", "event_time", "time_bucket", "logged_at",
    "category", "item", "quantity", "unit", "severity", "note", "detail",
)


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

_SQLITE_DDL = """
create table if not exists supplements (
    id text primary key, name text not null, brand text, form text,
    sort_order integer not null default 0, created_at text not null
);
create table if not exists supplement_regimens (
    id text primary key,
    supplement_id text not null references supplements(id),
    dose real, unit text, frequency text not null default '1/day',
    start_date text not null, end_date text,
    reason text, status text not null default 'active', notes text,
    created_at text not null
);
create table if not exists symptom_types (
    id text primary key, name text not null,
    active integer not null default 1, sort_order integer not null default 0,
    created_at text not null
);
create table if not exists habits (
    id text primary key, name text not null, kind text not null default 'binary',
    unit text, target real,
    active integer not null default 1, sort_order integer not null default 0,
    created_at text not null
);
create table if not exists events (
    id text primary key,
    event_date text not null, event_time text, time_bucket text,
    logged_at text not null,
    category text not null, item text, quantity real, unit text,
    severity integer, note text, detail text not null default '{}'
);
create table if not exists app_settings (
    key text primary key, value text
);
create index if not exists events_date_idx on events (event_date desc);
create index if not exists events_cat_date_idx on events (category, event_date desc);
create index if not exists regimen_supplement_idx on supplement_regimens (supplement_id);
create index if not exists regimen_status_idx on supplement_regimens (status);
"""

_BOOL_COLS = {"active"}


class SQLiteDB:
    kind = "sqlite"

    def __init__(self, path: str = "tracker.db"):
        self.path = path
        with self._conn() as c:
            c.executescript(_SQLITE_DDL)
        self._migrate()

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _migrate(self):
        """Bring an older tracker.db up to the current shape, in place."""
        with self._conn() as c:
            cols = {r["name"] for r in c.execute("pragma table_info(events)")}
            if "event_time" not in cols:
                c.execute("alter table events add column event_time text")

            scols = {r["name"] for r in c.execute("pragma table_info(supplements)")}
            if scols and "brand" not in scols:
                # Old flat supplements table: name/dose/frequency/active.
                # Lift each row into a catalog entry plus one open regimen.
                old = list(c.execute("select * from supplements"))
                c.execute("alter table supplements rename to supplements_pre_regimen")
                c.executescript(_SQLITE_DDL)
                today = date.today().isoformat()
                for r in old:
                    sid = r["id"]
                    c.execute(
                        "insert into supplements (id, name, brand, form, sort_order, created_at)"
                        " values (?,?,?,?,?,?)",
                        (sid, r["name"], None, None,
                         r["sort_order"] if "sort_order" in r.keys() else 0,
                         r["created_at"] if "created_at" in r.keys() else today))
                    c.execute(
                        "insert into supplement_regimens (id, supplement_id, dose, unit,"
                        " frequency, start_date, end_date, reason, status, notes, created_at)"
                        " values (?,?,?,?,?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), sid, None, None,
                         r["frequency"] if "frequency" in r.keys() else "1/day",
                         today, None, None,
                         "active" if (("active" not in r.keys()) or r["active"]) else "stopped",
                         # old `dose` was free text ("2000 IU"); the new column is
                         # numeric, so keep the original wording in notes
                         " ".join(x for x in [
                             "migrated from the pre-regimen supplement list;",
                             f"was: {r['dose']}" if ("dose" in r.keys() and r["dose"]) else ""
                         ] if x).strip(),
                         today))

    @staticmethod
    def _out(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for k in _BOOL_COLS & d.keys():
            d[k] = bool(d[k])
        if "detail" in d and isinstance(d["detail"], str):
            d["detail"] = json.loads(d["detail"] or "{}")
        return d

    # -- settings -----------------------------------------------------------

    def setting_get(self, key: str, default=None):
        with self._conn() as c:
            r = c.execute("select value from app_settings where key = ?", (key,)).fetchone()
        return r["value"] if r else default

    def setting_set(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute("insert into app_settings (key, value) values (?,?) "
                      "on conflict(key) do update set value = excluded.value",
                      (key, value))

    # -- reference tables ---------------------------------------------------

    def ref_list(self, table: str, active_only: bool = True) -> list[dict]:
        assert table in REF_TABLES
        q = f"select * from {table}"
        if active_only and table in ("symptom_types", "habits"):
            q += " where active = 1"
        q += " order by sort_order, name" if table in ("supplements", "symptom_types", "habits") \
             else " order by start_date desc"
        with self._conn() as c:
            return [self._out(r) for r in c.execute(q)]

    def ref_upsert(self, table: str, row: dict) -> str:
        assert table in REF_TABLES
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("created_at", datetime.now().isoformat())
        for k in _BOOL_COLS & row.keys():
            row[k] = int(bool(row[k]))
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        updates = ", ".join(f"{k}=excluded.{k}" for k in row if k != "id")
        with self._conn() as c:
            c.execute(
                f"insert into {table} ({cols}) values ({marks}) "
                f"on conflict(id) do update set {updates}",
                list(row.values()),
            )
        return row["id"]

    def ref_delete(self, table: str, row_id: str) -> None:
        assert table in REF_TABLES
        with self._conn() as c:
            if table == "supplements":
                c.execute("delete from supplement_regimens where supplement_id = ?", (row_id,))
            c.execute(f"delete from {table} where id = ?", (row_id,))

    def regimens_on(self, d: date) -> list[dict]:
        """Regimens in force on a given day, joined to the catalog.

        Membership is decided by the DATE SPAN, not by current status: a
        regimen that ran 2021-2025 and is now 'stopped' is exactly what you
        were taking in 2023, and that is the point of keeping regimen history.
        'paused' is the one status that removes a regimen from its own span,
        since a pause has no end_date to bound it.
        """
        q = """select r.*, s.name, s.brand, s.form
               from supplement_regimens r join supplements s on s.id = r.supplement_id
               where r.status <> 'paused'
                 and r.start_date <= ?
                 and (r.end_date is null or r.end_date >= ?)
               order by s.sort_order, s.name"""
        iso = d.isoformat()
        with self._conn() as c:
            return [self._out(r) for r in c.execute(q, (iso, iso))]

    # -- events -------------------------------------------------------------

    def events_insert(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        prepared = [_prepare_event(r, json_dumps=True) for r in rows]
        cols = ", ".join(_EVENT_FIELDS)
        marks = ", ".join("?" for _ in _EVENT_FIELDS)
        with self._conn() as c:
            c.executemany(
                f"insert into events ({cols}) values ({marks})",
                [[r[f] for f in _EVENT_FIELDS] for r in prepared],
            )
        return len(prepared)

    def events_fetch(self, start: date | None = None, end: date | None = None,
                     category: str | None = None, limit: int = 2000) -> list[dict]:
        q, params = "select * from events where 1=1", []
        if start:
            q += " and event_date >= ?"
            params.append(start.isoformat())
        if end:
            q += " and event_date <= ?"
            params.append(end.isoformat())
        if category:
            q += " and category = ?"
            params.append(category)
        q += " order by event_date desc, event_time, logged_at desc limit ?"
        params.append(limit)
        with self._conn() as c:
            return [self._out(r) for r in c.execute(q, params)]

    def event_delete(self, event_id: str) -> None:
        with self._conn() as c:
            c.execute("delete from events where id = ?", (event_id,))


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

class SupabaseDB:
    kind = "supabase"

    def __init__(self, url: str, key: str):
        from supabase import create_client  # imported lazily
        self.client = create_client(url, key)

    def setting_get(self, key: str, default=None):
        r = self.client.table("app_settings").select("value").eq("key", key).execute().data
        return r[0]["value"] if r else default

    def setting_set(self, key: str, value: str) -> None:
        self.client.table("app_settings").upsert({"key": key, "value": value}).execute()

    def ref_list(self, table: str, active_only: bool = True) -> list[dict]:
        assert table in REF_TABLES
        q = self.client.table(table).select("*")
        if active_only and table in ("symptom_types", "habits"):
            q = q.eq("active", True)
        if table == "supplement_regimens":
            return q.order("start_date", desc=True).execute().data
        return q.order("sort_order").order("name").execute().data

    def ref_upsert(self, table: str, row: dict) -> str:
        assert table in REF_TABLES
        row = dict(row)
        row.setdefault("id", str(uuid.uuid4()))
        self.client.table(table).upsert(row).execute()
        return row["id"]

    def ref_delete(self, table: str, row_id: str) -> None:
        assert table in REF_TABLES
        if table == "supplements":
            self.client.table("supplement_regimens").delete().eq("supplement_id", row_id).execute()
        self.client.table(table).delete().eq("id", row_id).execute()

    def regimens_on(self, d: date) -> list[dict]:
        iso = d.isoformat()
        rows = (self.client.table("supplement_regimens")
                .select("*, supplements(name, brand, form, sort_order)")
                .neq("status", "paused").lte("start_date", iso).execute().data)
        out = []
        for r in rows:
            if r.get("end_date") and r["end_date"] < iso:
                continue
            cat = r.pop("supplements", None) or {}
            r.update({k: cat.get(k) for k in ("name", "brand", "form")})
            r["_sort"] = cat.get("sort_order") or 0
            out.append(r)
        return sorted(out, key=lambda r: (r["_sort"], r.get("name") or ""))

    def events_insert(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        payload = [_prepare_event(r, json_dumps=False) for r in rows]
        self.client.table("events").insert(payload).execute()
        return len(payload)

    def events_fetch(self, start: date | None = None, end: date | None = None,
                     category: str | None = None, limit: int = 2000) -> list[dict]:
        q = self.client.table("events").select("*")
        if start:
            q = q.gte("event_date", start.isoformat())
        if end:
            q = q.lte("event_date", end.isoformat())
        if category:
            q = q.eq("category", category)
        return q.order("event_date", desc=True).limit(limit).execute().data

    def event_delete(self, event_id: str) -> None:
        self.client.table("events").delete().eq("id", event_id).execute()


# ---------------------------------------------------------------------------
# Shared helpers + factory
# ---------------------------------------------------------------------------

def _prepare_event(row: dict, json_dumps: bool) -> dict:
    """Normalise one event dict into the exact column set, filling defaults."""
    out = {f: None for f in _EVENT_FIELDS}
    out.update({k: v for k, v in row.items() if k in _EVENT_FIELDS})

    out["id"] = out["id"] or str(uuid.uuid4())
    out["logged_at"] = out["logged_at"] or datetime.now().isoformat()

    ed = out["event_date"] or date.today()
    out["event_date"] = ed.isoformat() if isinstance(ed, date) else str(ed)

    et = out["event_time"]
    if et is not None and not isinstance(et, str):
        et = et.strftime("%H:%M")          # datetime.time -> "HH:MM"
    out["event_time"] = et

    if not out["category"]:
        raise ValueError("event is missing a category")

    detail = out["detail"] or {}
    out["detail"] = json.dumps(detail) if json_dumps else detail
    return out


def _secret(name: str) -> str | None:
    """Look in Streamlit secrets first, then the environment."""
    try:
        import streamlit as st
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


def get_db():
    url, key = _secret("SUPABASE_URL"), _secret("SUPABASE_KEY")
    if url and key:
        return SupabaseDB(url, key)
    return SQLiteDB(os.environ.get("TRACKER_DB", "tracker.db"))
