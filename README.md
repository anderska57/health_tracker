# Health tracker — beta

Six logging panels (food, alcohol, symptoms, supplements, medications, habits)
over one event table. Python + Streamlit, SQLite locally, Supabase in production.

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

Deploy to Streamlit Community Cloud (free): connect the GitHub repo at
share.streamlit.io and paste your secrets into the app's Secrets box. Then on
your phone open the URL in Safari → Share → **Add to Home Screen**.

> Don't skip the Supabase step before deploying. Community Cloud containers are
> ephemeral — a SQLite file there is deleted on every restart.

### Passcode (optional)

Streamlit's own private-app sign-in goes through GitHub, which can be awkward on
a phone (two-factor screens don't always appear inside its redirect). The app
has its own gate instead. Add a third secret:

```toml
APP_PASSCODE = "four random words here"
```

With it set, the app shows only a passcode box until unlocked. With no
`APP_PASSCODE` (e.g. running locally) there is no gate.

**Remember this device** (on by default) stores a cookie so the device opens
straight in next time — including iPhone home-screen apps, which can't rely on
the URL because web-app mode launches the site's start address. The cookie
holds an HMAC of the passcode, not the passcode, so changing `APP_PASSCODE`
locks out every remembered device at once. **Lock this device** on the home
screen deletes the cookie. Browsers may still expire it on their own after a
stretch without use.

The passcode can also ride in the URL — `?k=<passcode>` — which works in a
normal browser tab but puts the passcode in the address itself.

The Supabase key never reaches the browser either way: Streamlit runs this
Python on the server and sends only the rendered page. Anyone holding the
passcode — or the home-screen URL that contains it — can read and write the
log, so treat it like a password.

## Tests

```bash
python3 test_smoke.py   # storage layer + every category's data shape
python3 test_app.py     # renders and clicks through every panel headlessly
```

## Data model

Everything lands in one `events` table:

| column | meaning |
|---|---|
| `event_date` | the day you're logging **about** |
| `event_time` | real clock time where one is meaningful (food); null elsewhere |
| `time_bucket` | morning / afternoon / evening / night (null where not meaningful) |
| `logged_at` | when the row was written — lets you tell live from retrospective logging |
| `category` | food / alcohol / symptom / supplement / medication / habit |
| `item` | drink type, food text, symptom name, supplement or medication name, habit name |
| `quantity`, `unit` | drinks, habit counts |
| `severity` | symptoms, 1–5 |
| `detail` | jsonb — category-specific extras (meal slot, habit kind, supplement exception) |

Adding another panel is a new `category` value, a tile in `PANELS`, and a
function in `app.py`. No migration.

## Layout

The home screen is a grid of tiles, three across, each showing a one-word
status for today. Every panel opens with the same two lines: a small back arrow
beside the title, then `◀  Today · Sat, Oct 10  ▶` (tap the date to jump to
any day). Save buttons sit **above** their list, and lists are one row per
item, so most panels fit on a phone screen without scrolling. Adding a new
item opens a pop-up rather than a form at the bottom of the page.

Colours live in `.streamlit/config.toml` — change them there and push. That
file also has a commented-out lighter palette to swap in.

### Per-category notes

**Alcohol.** One row per (type, quantity, time bucket). Tapping *wine* twice
gives two independent lines, so "2 glasses at dinner, 1 nightcap" stores
exactly that. `STANDARD_DRINK_REFERENCE` in `config.py` is displayed as
reference text only — you convert a real pour into standard drinks yourself.

**Supplements.** Two tables, carried over from `health.sqlite`: `supplements`
is the catalog (one row per product) and `supplement_regimens` is a stretch of
time you intended to take it a certain way — your *usual intake*. Stopping and
restarting later makes a new regimen row, so separate trials stay separable,
and the panel shows the regimen in force **on the date you are logging**. A
regimen belongs to a date by its span, not its current status; `paused` is the
one status that removes a regimen from its own span.

Usual intake is **assumed taken** — the same logic as a food-frequency
questionnaire's "usual intake". The panel lists that day's supplements already
switched on; you only record what differed, and only differences are stored:

| `detail.kind` | meaning |
|---|---|
| `skipped` | a daily regimen not taken that day |
| `dose_changed` | taken, at `quantity` instead of `detail.usual_dose` |
| `taken` | a non-daily regimen (`1/week`, `as needed`) taken that day |
| `extra` | a one-off, not part of any regimen |

A day with no rows means "usual intake, as listed". Non-daily regimens start
switched off, since there's no schedule to say which day they fall on. New
supplements can be added from the panel itself — tick *Part of my usual
intake* to start a regimen from that date, or leave it off for a one-off.
(Older data may also contain `day_confirmed` and `exception` rows from an
earlier version; the panel reads them and rewrites them on the next save.)

To load a regimen list from an existing `health.sqlite`:

```bash
python3 import_supplements.py supplements/health.sqlite
```

Idempotent — matches on name and on (supplement, start date, frequency), so
running it twice changes nothing.

**Medications.** Exactly the supplement model, on its own panel: the same two
tables, with `supplements.kind = 'medication'`, and day-level rows logged with
`category = 'medication'`. An *as needed* medication starts unchecked; check it
on the days you take it. A database created before medications existed needs
one line run in the Supabase SQL editor (the Medications panel shows it until
then):

```sql
alter table supplements add column if not exists kind text not null default 'supplement';
```

**Food.** Free text per eating occasion. Four occasions — breakfast, lunch,
dinner, snack — each carrying a real clock time, because the occasion alone was
ambiguous (dinner eaten at 10pm is still dinner). Picking an occasion prefills
its default time: 8:30am / 12:00pm / 6:00pm, editable under Settings -> Meal
times, with snack always defaulting to now. `time_bucket` is then derived from
the actual time, so food rows stay joinable with alcohol and symptom rows.
Deliberately unstructured otherwise — the entries stay codable later against
FNDDS/FDC when you decide how granular you want to be.

**Symptoms.** Tap one from your list, or add a new one right on the panel.
Pick severity and *every* time of day it applied. Each time of day is stored as
its own row so `time_bucket` stays queryable next to alcohol and food; the rows
share `detail.entry_id`, which ties them back into one entry.

**Habits.** Binary (checkbox) or count (number box). Saving replaces the day rather
than appending, so re-saving is safe.

## Known beta limitations

- Streamlit reruns the whole script on every tap, so each interaction is a
  server round-trip. Fine at a few taps a day; it will feel slow if food
  logging ever grows a searchable database. The storage layer is deliberately
  isolated in `db.py` so the UI can be replaced without touching the data.
- No offline mode. You need a connection to log.
- Single user, permissive RLS. Tighten `schema.sql` before anyone else touches
  the database.
