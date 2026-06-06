"""Task 6.9 — the revenue-anomaly detector training pipeline.

Proves the unsupervised detector satisfies the phase DoD without the DB:
  - it compares 3 candidates (robust z-score / IQR / IsolationForest, AD-6.2) and logs
    EACH to results.csv with detection F1 mean +- std and precision/recall in `extra`;
  - it scores them by INJECTING known anomalies (no real labels) and picks the best;
  - the saved artifact FLAGS an injected anomalous day and stays QUIET on normal days
    (the headline DoD), and treats a Ramadan spike as seasonal, not anomalous;
  - the card records the method + threshold so "what does it flag?" is answerable.

`train_from_frame` is pure (frame in, artifact out), fed a synthetic seasonal revenue
series through the REAL feature builder, with the artifact/results paths redirected to
tmp_path — the real results.csv and committed anomaly.joblib are never touched.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

import joblib
import pandas as pd
import pytest

from app.ml import seasonality
from app.ml.anomaly import train as anomaly_train
from app.ml.anomaly.model import REGRESSOR_FEATURES, SeasonalAnomalyDetector
from app.ml.features.anomaly import build_anomaly_features


def _revenue_series(*, days: int = 365, start: date = date(2024, 6, 1)) -> pd.DataFrame:
    """A smooth, seasonal daily-revenue series (weekend/Ramadan/summer lifts + mild noise)
    run through the real builder — so a seasonal model can learn it and normal-day
    residuals stay small."""
    rng = random.Random(42)
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        base = 1_000_000.0
        if seasonality.is_weekend(d):
            base *= 1.3
        if seasonality.is_ramadan(d):
            base *= 1.8
        if seasonality.is_summer(d):
            base *= 1.2
        rows.append((d, int(base * rng.uniform(0.92, 1.08))))
    return build_anomaly_features(rows)


@pytest.fixture
def out_paths(tmp_path) -> tuple[Path, Path, Path]:
    return tmp_path / "anomaly.joblib", tmp_path / "anomaly_card.json", tmp_path / "results.csv"


def _train(frame: pd.DataFrame, out_paths, *, n_tenants: int = 1):
    artifact, card, results = out_paths
    return anomaly_train.train_from_frame(
        frame, n_tenants=n_tenants, artifact=artifact, card_path=card, results_path=results
    )


def test_logs_three_candidates(out_paths) -> None:
    card = _train(_revenue_series(), out_paths)
    assert card is not None
    rows = pd.read_csv(out_paths[2])
    assert set(rows["model"]) == {"zscore", "iqr", "isolation_forest"}
    assert (rows["task"] == "anomaly").all()
    assert (rows["metric"] == "f1").all()
    for raw in rows["extra"]:
        assert set(json.loads(raw)) == {"precision", "recall"}


def test_winner_has_highest_f1(out_paths) -> None:
    card = _train(_revenue_series(), out_paths)
    rows = pd.read_csv(out_paths[2])
    assert card.model == rows.loc[rows["cv_mean"].idxmax(), "model"]


def test_artifact_flags_injected_anomaly_and_stays_quiet(out_paths) -> None:
    frame = _revenue_series()
    card = _train(frame, out_paths)
    assert card is not None
    detector: SeasonalAnomalyDetector = joblib.load(out_paths[0])

    # Quiet on normal days: only a small fraction of an ordinary year is flagged.
    assert detector.flag(frame).mean() < 0.15

    # A 10x revenue spike on an established-baseline day IS flagged.
    injected = frame.copy()
    idx = 320  # well past the trailing-baseline warm-up
    injected.loc[idx, "revenue"] = int(injected.loc[idx, "baseline_mean"] * 10 + 1)
    assert bool(detector.flag(injected)[idx])


def test_ramadan_spike_is_not_flagged_as_anomaly(out_paths) -> None:
    # The constitution's pitfall: a high-but-expected Ramadan day must NOT be flagged.
    frame = _revenue_series()
    _train(frame, out_paths)
    detector: SeasonalAnomalyDetector = joblib.load(out_paths[0])
    ramadan = frame[frame["is_ramadan"] == 1]
    assert not ramadan.empty
    # The seasonal model expects the Ramadan lift, so the flag rate on Ramadan days is low.
    assert detector.flag(ramadan).mean() < 0.2


def test_card_records_method_and_threshold(out_paths) -> None:
    card = _train(_revenue_series(), out_paths)
    assert card.task == "anomaly"
    assert card.target == "revenue"
    assert card.metric == "f1"
    assert card.data_source == "synthetic"
    assert card.features == REGRESSOR_FEATURES
    assert card.extra["method"] == card.model
    assert "threshold" in card.extra
    assert card.extra["precision"] >= 0 and card.extra["recall"] >= 0


def test_insufficient_data_trains_nothing(out_paths) -> None:
    short = _revenue_series(days=30)  # below MIN_ROWS
    assert _train(short, out_paths) is None
    assert not out_paths[0].exists()
