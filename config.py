"""Constants and small helpers shared across the app."""

from datetime import datetime

APP_TITLE = "Tracker"

# ---------------------------------------------------------------------------
# Time of day
# ---------------------------------------------------------------------------

TIME_BUCKETS = ["morning", "afternoon", "evening", "night"]

# (bucket, start_hour_inclusive, end_hour_inclusive)
_BUCKET_HOURS = [
    ("morning", 5, 11),
    ("afternoon", 12, 16),
    ("evening", 17, 21),
    ("night", 22, 4),  # wraps midnight
]


def current_bucket(now: datetime | None = None) -> str:
    """Time bucket derived from the clock. Used as the prefilled default."""
    h = (now or datetime.now()).hour
    for name, start, end in _BUCKET_HOURS:
        if start <= end:
            if start <= h <= end:
                return name
        else:  # night wraps past midnight
            if h >= start or h <= end:
                return name
    return "evening"


# ---------------------------------------------------------------------------
# Alcohol
# ---------------------------------------------------------------------------

DRINK_TYPES = ["wine", "seltzer", "whiskey", "gin", "beer", "other liquor"]

# Displayed as non-editable reference so you can convert a real pour into
# standard drinks yourself (a 10 oz glass of wine = 2 drinks).
STANDARD_DRINK_REFERENCE = {
    "wine": "5 oz (12% ABV)",
    "seltzer": "12 oz (5% ABV)",
    "whiskey": "1.5 oz (40% ABV)",
    "gin": "1.5 oz (40% ABV)",
    "beer": "12 oz (5% ABV)",
    "other liquor": "1.5 oz (40% ABV)",
}

# ---------------------------------------------------------------------------
# Food
# ---------------------------------------------------------------------------

# Four eating occasions. Each entry carries an actual clock time -- the
# occasion alone was ambiguous (dinner eaten at 10pm is still dinner), so the
# occasion says WHAT the eating event was and the time says WHEN.
MEAL_SLOTS = ["breakfast", "lunch", "dinner", "snack"]

# Default clock time prefilled when each occasion is picked. The first three
# are editable in Settings; "snack" always defaults to the current time.
DEFAULT_MEAL_TIMES = {
    "breakfast": "08:30",
    "lunch": "12:00",
    "dinner": "18:00",
}

# Settings keys under which the user's overrides are stored.
MEAL_TIME_SETTING = "meal_time_{slot}"

# ---------------------------------------------------------------------------
# Symptoms
# ---------------------------------------------------------------------------

SEVERITY_LABELS = {1: "1 · barely", 2: "2 · mild", 3: "3 · moderate", 4: "4 · bad", 5: "5 · severe"}

# Seeds used only if the symptom list is empty on first run. Edit in Settings.
DEFAULT_SYMPTOMS = ["bloating", "nausea", "headache", "fatigue", "reflux", "cramping"]

DEFAULT_HABITS = [
    ("exercise", "binary", None),
    ("steps", "count", "steps"),
    ("water", "count", "glasses"),
    ("meditation", "binary", None),
]

# ---------------------------------------------------------------------------
# Supplements
# ---------------------------------------------------------------------------

# Frequency is free text (matching the existing health.sqlite convention:
# "1/day", "3/week", "as needed"), with these offered as quick picks.
SUPPLEMENT_FREQUENCIES = ["1/day", "2/day", "1/week", "2/week", "3/week", "as needed"]

REGIMEN_STATUSES = ["active", "paused", "stopped"]

SUPPLEMENT_FORMS = ["capsule", "tablet", "softgel", "powder", "liquid",
                    "gummy", "suppository", "other"]

# Exception kinds recorded against a confirmed day
SUPPLEMENT_EXCEPTIONS = ["skipped", "extra"]


def is_daily(frequency: str) -> bool:
    """Daily regimens belong in the one-tap 'usual stack' confirmation.
    Anything less frequent is logged as a positive event instead, because
    'I didn't take my weekly suppository today' is not a miss."""
    f = (frequency or "").strip().lower()
    return f in ("daily", "every day") or f.endswith("/day") or f.endswith("/d")

CATEGORIES = ["food", "alcohol", "symptom", "supplement", "habit"]
