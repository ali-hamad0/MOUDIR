"""Task 6.11 — golden evals catch a regression.

Proves the committed golden sets + thresholds gate the REAL artifacts:
  - each model clears its threshold on its frozen golden set (so a normal build is green);
  - a model BROKEN on purpose fails its eval (so a regression turns CI red — the DoD);
  - a missing artifact is SKIPPED, never failed (CI without the artifact stays green).

These run in the offline suite (no DB, no Settings) — the evaluator loads artifacts + golden
JSON straight from disk, exactly as the CI `python -m app.ml.eval` step does.
"""

import joblib
import numpy as np
import pytest
from sklearn.dummy import DummyClassifier, DummyRegressor

from app.ml.eval import evaluate
from app.ml.eval.evaluate import (
    evaluate_anomaly,
    evaluate_churn,
    evaluate_demand,
    load_thresholds,
)
from app.ml.features.churn import feature_columns as churn_features
from app.ml.features.demand import feature_columns as demand_features


@pytest.fixture
def thresholds() -> dict:
    return load_thresholds()


# ── thresholds file ──────────────────────────────────────────────────────────


def test_thresholds_file_has_every_model(thresholds) -> None:
    assert thresholds["demand"]["mae_max"] > 0
    assert 0 < thresholds["churn"]["recall_pos_min"] <= 1
    assert 0 < thresholds["anomaly"]["recall_pos_min"] <= 1


# ── the real artifacts clear their golden thresholds ─────────────────────────


def test_demand_meets_threshold(thresholds) -> None:
    r = evaluate_demand(thresholds)
    if r.skipped:
        pytest.skip("demand.joblib absent")
    assert r.passed, r.metrics
    assert r.metrics["mae"] <= thresholds["demand"]["mae_max"]


def test_churn_meets_threshold(thresholds) -> None:
    r = evaluate_churn(thresholds)
    if r.skipped:
        pytest.skip("churn.joblib absent")
    assert r.passed, r.metrics
    assert r.metrics["recall_pos"] >= thresholds["churn"]["recall_pos_min"]


def test_anomaly_meets_threshold(thresholds) -> None:
    r = evaluate_anomaly(thresholds)
    if r.skipped:
        pytest.skip("anomaly.joblib absent")
    assert r.passed, r.metrics
    assert r.metrics["recall_pos"] >= thresholds["anomaly"]["recall_pos_min"]


# ── break a model on purpose → the eval fails (the DoD) ──────────────────────


class _AllNormal:
    """Stand-in for a broken anomaly artifact: flags nothing → recall 0."""

    def flag(self, frame) -> np.ndarray:
        return np.zeros(len(frame), dtype=bool)


def test_broken_demand_model_fails(thresholds, tmp_path) -> None:
    # A model that always predicts 0 blows past the MAE ceiling.
    broken = DummyRegressor(strategy="constant", constant=0.0)
    broken.fit(np.zeros((2, len(demand_features()))), [0, 0])
    path = tmp_path / "demand.joblib"
    joblib.dump(broken, path)
    assert evaluate_demand(thresholds, artifact=path).passed is False


def test_broken_churn_model_fails(thresholds, tmp_path) -> None:
    # A model that never predicts churn → recall on the positive class is 0.
    broken = DummyClassifier(strategy="constant", constant=0)
    broken.fit(np.zeros((2, len(churn_features()))), [0, 1])
    path = tmp_path / "churn.joblib"
    joblib.dump(broken, path)
    assert evaluate_churn(thresholds, artifact=path).passed is False


def test_broken_anomaly_model_fails(thresholds, tmp_path) -> None:
    path = tmp_path / "anomaly.joblib"
    joblib.dump(_AllNormal(), path)
    assert evaluate_anomaly(thresholds, artifact=path).passed is False


# ── a missing artifact is skipped, not failed ────────────────────────────────


def test_missing_artifact_is_skipped(thresholds, tmp_path) -> None:
    r = evaluate_demand(thresholds, artifact=tmp_path / "nope.joblib")
    assert r.skipped and r.passed


def test_run_returns_all_three(thresholds) -> None:
    results = evaluate.run(thresholds)
    assert {r.model for r in results} == {"demand", "churn", "anomaly"}
