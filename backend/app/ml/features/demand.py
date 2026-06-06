"""Demand features (Phase 6, Task 6.4). Per-product daily demand → a leakage-free
feature table for the regressor (Task 6.5).

The cardinal rule (AD-6.6, the constitution's "most common bug"): a feature for a
target day D may use demand only from days STRICTLY BEFORE D. Calendar facts about D
itself (its weekday, whether it is Ramadan/summer/payday) ARE known in advance, so they
are legitimate features for D — only the *demand quantities* must be historical. This
split is the whole game: planted seasonality (Task 6.2) becomes learnable signal here,
without the model ever peeking at the answer.

Pure functions over in-memory rows (the output of TrainingDataRepository.daily_product_
demand): no DB, no session — so they're unit-testable and run identically on Colab from
the exported CSVs ([[phase6-ml-colab-decision]]).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from app.ml import seasonality

# Lag/rolling windows (in days) the regressor gets as features. All are computed from
# days strictly before the target, so none can leak.
LAG_DAYS = (1, 7, 14)
ROLLING_WINDOWS = (7, 28)


@dataclass(frozen=True)
class DemandRow:
    """One (product, day, units) observation — the daily_product_demand read shape."""

    product_id: str
    day: date
    units: int


def _to_rows(records: Sequence) -> list[DemandRow]:
    """Accept either DemandRow or a raw (product_id, day, units) row/tuple."""
    rows: list[DemandRow] = []
    for r in records:
        if isinstance(r, DemandRow):
            rows.append(r)
        else:
            rows.append(DemandRow(product_id=str(r[0]), day=r[1], units=int(r[2])))
    return rows


def _seasonality_features(day: date) -> dict[str, int]:
    """Calendar features for a target day — knowable in advance, hence leak-free."""
    return {
        "day_of_week": seasonality.day_of_week(day),
        "is_weekend": int(seasonality.is_weekend(day)),
        "is_ramadan": int(seasonality.is_ramadan(day)),
        "is_summer": int(seasonality.is_summer(day)),
        "is_payday": int(seasonality.is_payday_window(day)),
        "month": day.month,
    }


def build_demand_features(records: Sequence, *, as_of: date | None = None) -> pd.DataFrame:
    """Build the demand feature table from per-product daily history.

    One output row per (product, day) for every day that has at least one prior day of
    history (so the lags exist). Columns:
      product_id, day, <seasonality features>, lag_{n}, roll_mean_{w}, roll_std_{w},
      and `units` (the regression target for that day).

    `as_of` (exclusive) drops every day on or after it BEFORE any feature is computed —
    the leakage boundary (AD-6.6). With as_of=None the whole series is used (training on
    all history); a backtest/prediction passes the cutoff so nothing future is seen.
    """
    rows = _to_rows(records)
    if as_of is not None:
        rows = [r for r in rows if r.day < as_of]
    if not rows:
        return _empty_frame()

    df = pd.DataFrame(
        [(r.product_id, r.day, r.units) for r in rows], columns=["product_id", "day", "units"]
    )
    df = df.sort_values(["product_id", "day"]).reset_index(drop=True)

    # Reindex each product onto a CONTIGUOUS daily calendar so lags/rolling windows mean
    # "N days ago", not "N observations ago" (a gap day = 0 demand, not skipped).
    pieces: list[pd.DataFrame] = []
    for product_id, g in df.groupby("product_id", sort=False):
        full = _dense_daily(g, product_id)
        _add_lag_features(full)
        pieces.append(full)

    out = pd.concat(pieces, ignore_index=True)
    # The first day of each product's history has no prior day → its lag_1 is NaN; drop
    # rows where the core lag is unknown (can't predict the very first day honestly).
    out = out.dropna(subset=[f"lag_{LAG_DAYS[0]}"]).reset_index(drop=True)
    return out


def _dense_daily(group: pd.DataFrame, product_id: str) -> pd.DataFrame:
    """Fill missing calendar days with 0 demand and attach seasonality features."""
    start, end = group["day"].min(), group["day"].max()
    all_days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    units_by_day = dict(zip(group["day"], group["units"], strict=True))

    records = []
    for d in all_days:
        feats = {"product_id": product_id, "day": d, "units": int(units_by_day.get(d, 0))}
        feats.update(_seasonality_features(d))
        records.append(feats)
    return pd.DataFrame(records)


def _add_lag_features(frame: pd.DataFrame) -> None:
    """Add lag_{n} and rolling mean/std of past demand — IN PLACE, all shifted so they
    only ever see days strictly before the row's day (no leakage)."""
    units = frame["units"]
    for n in LAG_DAYS:
        frame[f"lag_{n}"] = units.shift(n)
    for w in ROLLING_WINDOWS:
        # shift(1) first → the window ends YESTERDAY, never including today's target.
        past = units.shift(1)
        frame[f"roll_mean_{w}"] = past.rolling(window=w, min_periods=1).mean()
        frame[f"roll_std_{w}"] = past.rolling(window=w, min_periods=1).std().fillna(0.0)


def feature_columns() -> list[str]:
    """The model's input columns (everything except ids, the day, and the target)."""
    cols = list(_seasonality_features(date(2024, 1, 1)).keys())
    cols += [f"lag_{n}" for n in LAG_DAYS]
    for w in ROLLING_WINDOWS:
        cols += [f"roll_mean_{w}", f"roll_std_{w}"]
    return cols


def _empty_frame() -> pd.DataFrame:
    cols = ["product_id", "day", "units", *feature_columns()]
    return pd.DataFrame(columns=cols)
