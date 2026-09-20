# Health tracker — beta

Five logging panels (food, alcohol, symptoms, supplements, habits) over one
event table. Python + Streamlit, SQLite locally, Supabase in production.

## Run it locally right now

```bash
pip install -r requirements.txt
streamlit run app.py
```

No config needed — it falls back to a local `tracker.db` SQLite file. Open
Settings → Export → **Seed starter symptom & habit lists**, then edit those
lists and add your supplements.

## Point it at Supabase (~10 minutes)

1. Create a free project at supabase.com.
2. SQL Editor → paste all of `schema.sql` → Run.
3. Settings → API → copy the **Project URL** and the **anon public** key.
4. Create `.streamlit/secrets.toml`:

   ```toml
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "eyJ..."
   ```

The app switches backends automatically when those two secrets are present —
the home screen footer tells you which one is live.

5. Move what you have already logged up to Supabase:

   ```bash
   python3 migrate_to_supabase.py --dry-run   # shows what would move
   python3 migrate_to_supabase.py            # does it
   ```

   Row ids carry across, and every write is an upsert keyed on id, so running
   it twice changes nothing.

## Get it on your phone

Deploy to Streamlit Community Cloud (free): push this folder to a private
GitHub repo, connect it at share.streamlit.io, and paste the same two secrets
into the app's Secrets box. Then on your phone open the URL → Share → **Add to
Home Screen**. It launches full-screen like an app.

> Don't skip the Supabase step before deploying. Community Cloud containers are
> ephemeral — a SQLite file there is deleted on every restart.

## Tests

```bash
python3 test_smoke.py   # storage layer + every category's data shape
python3 test_app.py     # renders and clicks through all five panels headlessly
```

## Data model

Everything lands in one `events` table:

| column | meaning |
|---|---|
| `event_date` | the day you're logging **about** |
| `event_time` | real clock time where one is meaningful (food); null elsewhere |
| `time_bucket` | morning / afternoon / evening / night (null where not meaningful) |
| `logged_at` | when the row was written — lets you tell live from retrospective logging |
| `category` | food / alcohol / symptom / supplement / habit |
| `item` | drink type, food text, symptom name, supplement name, habit name |
| `quantity`, `unit` | drinks, habit counts |
| `severity` | symptoms, 1–5 |
| `detail` | jsonb — category-specific extras (meal slot, habit kind, supplement exception) |

Adding a sixth panel is a new `category` value plus a function in `app.py`. No
migration.

### Per-category notes

**Alcohol.** One row per (type, quantity, time bucket). Tapping *wine* twice
gives two independent lines, so "2 glasses at dinner, 1 nightcap" stores
exactly that. `STANDARD_DRINK_REFERENCE` in `config.py` is displayed as
reference text only — you convert a real pour into standard drinks yourself.

**Supplements.** Two tables, carried over from `health.sqlite`: `supplements`
is the catalog (one row per product) and `supplement_regimens` is a stretch of
time you intended to take it a certain way. Stopping and restarting later makes
a new regimen row, so separate trials stay separable — and the panel shows the
regimen that was in force **on the date you are logging**, not today's. A
regimen belongs to a date by its span, not its current status; `paused` is the
one status that removes a regimen from its own span.

Daily regimens (`1/day`, `2/day`, `daily`) go in the one-tap "took my usual
stack" confirmation, where you mark only what differed. This matters: if you
logged *only* misses, a day with no rows would be ambiguous between "took
everything" and "never opened the app," and adherence would drift upward over
months. The confirmation row makes an unconfirmed day explicitly **unknown**,
so it can be excluded from the denominator instead of counted as perfect. It
also records which regimen ids were expected that day.

Less-frequent regimens (`1/week`, `as needed`) are *not* part of that
confirmation — "I didn't take my weekly suppository today" is not a miss. They
get their own opt-in toggle and are recorded as positive events when taken.

To load a regimen list from an existing `health.sqlite`:

```bash
python3 import_supplements.py supplements/health.sqlite
```

Idempotent — matches on name and on (supplement, start date, frequency), so
running it twice changes nothing.

**Food.** Free text per eating occasion. Four occasions — breakfast, lunch,
dinner, snack — each carrying a real clock time, because the occasion alone was
ambiguous (dinner eaten at 10pm is still dinner). Picking an occasion prefills
its default time: 8:30am / 12:00pm / 6:00pm, editable under Settings -> Meal
times, with snack always defaulting to now. `time_bucket` is then derived from
the actual time, so food rows stay joinable with alcohol and symptom rows.
Deliberately unstructured otherwise — the entries stay codable later against
FNDDS/FDC when you decide how granular you want to be.

**Habits.** Binary (toggle) or count (stepper). Saving replaces the day rather
than appending, so re-saving is safe.

## Known beta limitations

- Streamlit reruns the whole script on every tap, so each interaction is a
  server round-trip. Fine at a few taps a day; it will feel slow if food
  logging ever grows a searchable database. The storage layer is deliberately
  isolated in `db.py` so the UI can be replaced without touching the data.
- No offline mode. You need a connection to log.
- Single user, permissive RLS. Tighten `schema.sql` before anyone else touches
  the database.
