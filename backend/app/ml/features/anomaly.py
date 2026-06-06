"""Anomaly features (Phase 6, Task 6.4). Daily revenue → a seasonality-aware table for
the revenue-anomaly detector (Task 6.9).

The detector asks "is today's revenue weird FOR THIS KIND OF DAY?" — so the features
carry the calendar context that explains normal variation (weekend, Ramadan, summer,
payday) plus a trailing baseline of RECENT revenue. The constitution's pitfall is
explicit: "treating Ramadan as a normal month" — a Ramadan spike is seasonality, not an
anomaly, so the model must SEE the Ramadan flag to avoid screaming every Ramadan day.

Leakage boundary (AD-6.6): the trailing baseline (mean/std of revenue) is computed from
days STRICTLY BEFORE each row's day; the day's own calendar flags are knowable in
advance. With `as_of` set, days on/after it are dropped before anything is computed.

Pure functions over the daily_revenue read shape (or raw (day, total_lbp) tuples).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from app.ml import seasonality

# Trailing window for the "normal recent revenue" baseline (days), ending yesterday.
BASELINE_WINDOW = 28


@dataclass(frozen=True)
class RevenueRow:
    day: date
    total_lbp: int


def _to_rows(records: Sequence) -> list[RevenueRow]:
    out: list[RevenueRow] = []
    for r in records:
        if isinstance(r, RevenueRow):
            out.append(r)
        else:
            out.append(RevenueRow(day=r[0], total_lbp=int(r[1])))
    return out


def build_anomaly_features(records: Sequence, *, as_of: date | None = None) -> pd.DataFrame:
    """Build the anomaly feature table from the daily-revenue series.

    One row per day (densified over the calendar so gaps are 0-revenue days). Columns:
      day, revenue, <seasonality flags>, baseline_mean, baseline_std, deviation
      (revenue - baseline_mean). `as_of` (exclusive) bounds the history first.

    The deviation + baseline are what an unsupervised detector / threshold consumes; the
    seasonality flags let it learn that high Ramadan revenue is expected, not anomalous.
    """
    rows = _to_rows(records)
    if as_of is not None:
        rows = [r for r in rows if r.day < as_of]
    if not rows:
        return _empty_frame()

    rows.sort(key=lambda r: r.day)
    start, end = rows[0].day, rows[-1].day
    rev_by_day = {r.day: r.total_lbp for r in rows}

    records_out = []
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        feats = {"day": d, "revenue": int(rev_by_day.get(d, 0))}
        feats.update(_seasonality_features(d))
        records_out.append(feats)

    df = pd.DataFrame(records_out)
    # Trailing baseline ending YESTERDAY (shift(1)) — never includes today's value.
    past = df["revenue"].shift(1)
    df["baseline_mean"] = past.rolling(window=BASELINE_WINDOW, min_periods=1).mean().fillna(0.0)
    df["baseline_std"] = past.rolling(window=BASELINE_WINDOW, min_periods=1).std().fillna(0.0)
    df["deviation"] = df["revenue"] - df["baseline_mean"]
    return df


def _seasonality_features(day: date) -> dict[str, int]:
    return {
        "day_of_week": seasonality.day_of_week(day),
        "is_weekend": int(seasonality.is_weekend(day)),
        "is_ramadan": int(seasonality.is_ramadan(day)),
        "is_summer": int(seasonality.is_summer(day)),
        "is_payday": int(seasonality.is_payday_window(day)),
        "month": day.month,
    }


def feature_columns() -> list[str]:
    """The detector's input columns (everything except the day and raw revenue)."""
    cols = list(_seasonality_features(date(2024, 1, 1)).keys())
    cols += ["baseline_mean", "baseline_std", "deviation"]
    return cols


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["day", "revenue", *feature_columns()])
