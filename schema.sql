-- Health tracker schema (Postgres / Supabase)
-- Run this once in the Supabase SQL editor.
--
-- Design note: one wide `events` table serves all five categories. Category-specific
-- fields live in `detail` (jsonb) so a new panel never needs a migration. The columns
-- that are promoted out of jsonb -- event_date, event_time, time_bucket, category,
-- item, quantity -- are the ones you will group by, so they stay indexable.
--
-- Supplements use a catalog/regimen split (carried over from health.sqlite): a
-- supplement is stored once; a regimen is a stretch of time you intended to take
-- it a certain way. Restarting after a break = a new regimen row, so separate
-- trials stay separable and "what was I on last March?" is answerable.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Reference tables: the things you define once and then log against
-- ---------------------------------------------------------------------------

create table if not exists supplements (
    id          uuid primary key default gen_random_uuid(),
    name        text not null unique,
    brand       text,
    form        text,                 -- capsule, powder, softgel, suppository...
    sort_order  int not null default 0,
    created_at  timestamptz not null default now()
);

create table if not exists supplement_regimens (
    id             uuid primary key default gen_random_uuid(),
    supplement_id  uuid not null references supplements(id) on delete cascade,
    dose           numeric,
    unit           text,                            -- mg, mcg, IU, capsule, scoop
    frequency      text not null default '1/day',   -- "1/day", "3/week", "as needed"
    start_date     date not null,
    end_date       date,                            -- null = ongoing
    reason         text,
    status         text not null default 'active'
                   check (status in ('active','paused','stopped')),
    notes          text,
    created_at     timestamptz not null default now()
);

create table if not exists symptom_types (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    active      boolean not null default true,
    sort_order  int not null default 0,
    created_at  timestamptz not null default now()
);

create table if not exists habits (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    kind        text not null default 'binary',  -- binary | count
    unit        text,                            -- "min", "pages", "glasses"
    target      numeric,
    active      boolean not null default true,
    sort_order  int not null default 0,
    created_at  timestamptz not null default now()
);

create table if not exists app_settings (
    key    text primary key,
    value  text
);

-- ---------------------------------------------------------------------------
-- The event log: every panel writes here
-- ---------------------------------------------------------------------------

create table if not exists events (
    id           uuid primary key default gen_random_uuid(),

    -- when the thing happened (the day you are logging ABOUT)
    event_date   date not null,
    event_time   time,           -- food carries a real clock time; others may not
    time_bucket  text,           -- morning | afternoon | evening | night | null

    -- when the row was created (audit trail; live vs retrospective logging)
    logged_at    timestamptz not null default now(),

    category     text not null,  -- alcohol | food | symptom | supplement | habit
    item         text,           -- drink type, food text, symptom, supplement, habit
    quantity     numeric,
    unit         text,
    severity     int,            -- symptoms only, 1-5
    note         text,
    detail       jsonb not null default '{}'::jsonb
);

create index if not exists events_date_idx      on events (event_date desc);
create index if not exists events_cat_date_idx  on events (category, event_date desc);
create index if not exists events_item_idx      on events (category, item);
create index if not exists regimen_supp_idx     on supplement_regimens (supplement_id);
create index if not exists regimen_status_idx   on supplement_regimens (status);

-- ---------------------------------------------------------------------------
-- Already ran an earlier version of this file? These bring it up to date.
-- Safe to run repeatedly.
-- ---------------------------------------------------------------------------

alter table events      add column if not exists event_time time;
alter table supplements add column if not exists brand text;
alter table supplements add column if not exists form  text;

-- ---------------------------------------------------------------------------
-- Single-user mode: RLS on, one permissive policy for the authenticated key.
-- Tighten this (add a user_id column + per-user policies) before other people
-- ever touch this database.
-- ---------------------------------------------------------------------------

alter table events              enable row level security;
alter table supplements         enable row level security;
alter table supplement_regimens enable row level security;
alter table symptom_types       enable row level security;
alter table habits              enable row level security;
alter table app_settings        enable row level security;

do $$
declare t text;
begin
  foreach t in array array['events','supplements','supplement_regimens',
                           'symptom_types','habits','app_settings'] loop
    execute format('drop policy if exists %I on %I', t || '_all', t);
    execute format(
      'create policy %I on %I for all using (true) with check (true)', t || '_all', t);
  end loop;
end $$;
