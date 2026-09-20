-- Personal Health Database Schema
-- Designed to be reproducible: schema and data are fully separated.
-- Domains covered so far: supplements, physical activity, symptoms,
-- tied together by periodic check-ins. Labs / Apple Health / diet are
-- future extensions that will plug into this same structure.

PRAGMA foreign_keys = ON;

-- ============================================================
-- CHECK-IN FRAMEWORK
-- Ties supplements / activity / symptoms together on a shared cadence
-- ============================================================

-- One row per trackable domain, storing the target review cadence.
-- 'enabled' lets you turn a domain off without deleting history.
CREATE TABLE checkin_settings (
    domain          TEXT PRIMARY KEY CHECK (domain IN ('supplements','activity','symptoms')),
    cadence_days    INTEGER NOT NULL DEFAULT 30,
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    last_updated    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per review cycle. completed_at is NULL until you actually
-- do the check-in, so overdue/upcoming check-ins are queryable.
CREATE TABLE checkin_periods (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    completed_at    TEXT,
    notes           TEXT
);

-- ============================================================
-- SUPPLEMENTS
-- ============================================================

-- Catalog: each supplement stored once, referenced everywhere else.
CREATE TABLE supplements (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    brand   TEXT,
    form    TEXT              -- capsule, powder, liquid, gummy, etc.
);

-- A stretch of time you intended to take something a certain way.
-- end_date NULL = still ongoing. Re-starting after a stop = new row,
-- so distinct "trials" of a supplement are preserved as separate data.
CREATE TABLE supplement_regimens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    supplement_id   INTEGER NOT NULL REFERENCES supplements(id),
    dose            REAL,
    unit            TEXT,             -- mg, mcg, IU, g, ml, etc.
    frequency       TEXT NOT NULL,    -- free text: "1/day", "3/week", "as needed"
    start_date      TEXT NOT NULL,
    end_date        TEXT,             -- NULL = ongoing
    reason          TEXT,             -- why you're taking it
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','stopped')),
    notes           TEXT
);

-- Periodic self-rated adherence, linked to a check-in period so you
-- get a timeseries rather than one static number.
CREATE TABLE supplement_adherence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    checkin_period_id   INTEGER NOT NULL REFERENCES checkin_periods(id),
    regimen_id          INTEGER NOT NULL REFERENCES supplement_regimens(id),
    adherence_pct       INTEGER NOT NULL CHECK (adherence_pct BETWEEN 0 AND 100 AND adherence_pct % 10 = 0),
    notes               TEXT,
    UNIQUE (checkin_period_id, regimen_id)
);

-- ============================================================
-- PHYSICAL ACTIVITY
-- ============================================================

CREATE TABLE activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,   -- running, lifting, yoga, walking...
    category    TEXT                    -- cardio, strength, flexibility, sport, etc.
);

CREATE TABLE activity_review (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    checkin_period_id       INTEGER NOT NULL REFERENCES checkin_periods(id),
    activity_id             INTEGER NOT NULL REFERENCES activities(id),
    typical_frequency       TEXT,       -- "3x/week", "daily", "1x/month"
    typical_duration_min    INTEGER,
    notes                   TEXT,
    UNIQUE (checkin_period_id, activity_id)
);

-- ============================================================
-- SYMPTOMS
-- ============================================================

CREATE TABLE symptoms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,   -- headache, fatigue, joint pain...
    category    TEXT
);

CREATE TABLE symptom_review (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    checkin_period_id   INTEGER NOT NULL REFERENCES checkin_periods(id),
    symptom_id          INTEGER NOT NULL REFERENCES symptoms(id),
    frequency           TEXT,           -- "daily", "a few times", "once"
    severity            INTEGER CHECK (severity BETWEEN 1 AND 10),
    notes               TEXT,
    UNIQUE (checkin_period_id, symptom_id)
);

-- ============================================================
-- Seed default cadence settings (monthly, matches current decision)
-- ============================================================
INSERT INTO checkin_settings (domain, cadence_days, enabled) VALUES
    ('supplements', 30, 1),
    ('activity',    30, 1),
    ('symptoms',    30, 1);

-- ============================================================
-- Helpful indexes
-- ============================================================
CREATE INDEX idx_regimen_supplement ON supplement_regimens(supplement_id);
CREATE INDEX idx_regimen_status ON supplement_regimens(status);
CREATE INDEX idx_adherence_period ON supplement_adherence(checkin_period_id);
CREATE INDEX idx_adherence_regimen ON supplement_adherence(regimen_id);
CREATE INDEX idx_activity_review_period ON activity_review(checkin_period_id);
CREATE INDEX idx_symptom_review_period ON symptom_review(checkin_period_id);
