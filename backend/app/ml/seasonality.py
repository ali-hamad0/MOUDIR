"""Lebanese seasonality — the shared truth for the seeder AND the feature builders.

Task 6.2 uses these to SHAPE synthetic demand; Task 6.4 will use the SAME helpers to
build FEATURES from real/seeded data. Keeping one definition means the signal we plant
is the signal we later learn — no drift between "how the data was made" and "what the
model is told about the calendar". Pure date functions, no DB, no randomness.

The windows are deliberately data-driven constants (not a hijri library): Modir trains
on a fixed historical window (Jun 2024 – May 2025, Task 6.2), so the relevant Ramadan
and summer ranges are known Gregorian spans. A future window adds its ranges here.
"""

from datetime import date

# Ramadan (Gregorian) spans that fall in or near Modir's training windows. Approximate
# civil dates — good enough for a seasonality FLAG (the model learns "this period is
# different", not an astronomical boundary). Extend when the training window moves.
RAMADAN_SPANS: tuple[tuple[date, date], ...] = (
    (date(2024, 3, 11), date(2024, 4, 9)),  # Ramadan 1445
    (date(2025, 3, 1), date(2025, 3, 30)),  # Ramadan 1446
    (date(2026, 2, 18), date(2026, 3, 19)),  # Ramadan 1447
)

# Summer-mountain season: Lebanese coastal shops dip while mountain/resort shops lift
# as people leave the city for the cooler high villages. A simple month window.
SUMMER_MONTHS: frozenset[int] = frozenset({6, 7, 8})


def is_ramadan(day: date) -> bool:
    """True if the date falls within a known Ramadan span (inclusive)."""
    return any(start <= day <= end for start, end in RAMADAN_SPANS)


def is_summer(day: date) -> bool:
    """True in Jun/Jul/Aug — the summer-mountain season."""
    return day.month in SUMMER_MONTHS


def is_payday_window(day: date) -> bool:
    """True around month-end / start (the 28th–31st and 1st–2nd).

    Lebanese salaries land at month boundaries; spending bumps then. A small,
    recurring calendar effect distinct from weekly rhythm.
    """
    return day.day >= 28 or day.day <= 2


def is_weekend(day: date) -> bool:
    """Lebanese weekend skew: Saturday(5)/Sunday(6) busier for most retail.

    weekday(): Mon=0 ... Sun=6.
    """
    return day.weekday() >= 5


def day_of_week(day: date) -> int:
    """Mon=0 ... Sun=6 — the raw categorical the feature builder one-hots later."""
    return day.weekday()
