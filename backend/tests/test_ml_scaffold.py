"""Task 6.1 — the ML scaffold: the prediction seam + the experiment log.

Proves the Phase 6 scaffold is sound and OFFLINE by default (Constitution IV +
AD-6.3): `ml_mode="stub"` returns deterministic stub predictors needing no artifact,
a stub returns None so callers fall back to their documented default, and
`ml_mode="trained"` with a MISSING artifact degrades to the stub (logged) rather than
crashing — the api must never fail to start because a model file is absent. Also
proves the results.csv helper writes the committed header and appends a row (the
"log every experiment" rule), without dragging in pandas.

The trained-artifact PRESENT branch is exercised by the per-model serve tasks
(6.6/6.8/6.9), which fill in the real predictors behind these same factories.
"""

import csv
from pathlib import Path
from uuid import uuid4

from app.infra.settings import Settings
from app.ml.predictors import (
    StubAnomalyDetector,
    StubChurnPredictor,
    StubDemandPredictor,
    build_anomaly_detector,
    build_churn_predictor,
    build_demand_predictor,
)
from app.ml.results import FIELDNAMES, ExperimentResult, ensure_results_file, log_result

# ── the seam ─────────────────────────────────────────────────────────────────


def test_stub_mode_returns_stub_predictors() -> None:
    settings = Settings.model_construct(ml_mode="stub")
    assert isinstance(build_demand_predictor(settings), StubDemandPredictor)
    assert isinstance(build_churn_predictor(settings), StubChurnPredictor)
    assert isinstance(build_anomaly_detector(settings), StubAnomalyDetector)


def test_stub_predictors_return_none_so_callers_fall_back() -> None:
    # None = "no signal"; the caller (e.g. forecast_demand) uses its documented default.
    assert StubDemandPredictor().predict_quantity(uuid4(), uuid4(), []) is None
    assert StubChurnPredictor().predict_risk(uuid4(), uuid4()) is None
    assert StubAnomalyDetector().is_anomalous(uuid4(), 1000.0) is None


def test_trained_mode_missing_artifact_degrades_to_stub() -> None:
    # "trained" but the artifact file does not exist -> stub (logged), never a crash.
    settings = Settings.model_construct(
        ml_mode="trained",
        ml_demand_artifact="does-not-exist-demand.joblib",
        ml_churn_artifact="does-not-exist-churn.joblib",
        ml_anomaly_artifact="does-not-exist-anomaly.joblib",
    )
    assert isinstance(build_demand_predictor(settings), StubDemandPredictor)
    assert isinstance(build_churn_predictor(settings), StubChurnPredictor)
    assert isinstance(build_anomaly_detector(settings), StubAnomalyDetector)


# ── the experiment log ───────────────────────────────────────────────────────


def test_ensure_results_file_writes_committed_header(tmp_path: Path) -> None:
    path = tmp_path / "results.csv"
    ensure_results_file(path)
    with path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == FIELDNAMES


def test_log_result_appends_a_row(tmp_path: Path) -> None:
    path = tmp_path / "results.csv"
    log_result(
        ExperimentResult(
            task="demand",
            model="ridge",
            metric="MAE",
            cv_mean=12.3,
            cv_std=1.1,
            n_splits=5,
            data_source="synthetic",
        ),
        path=path,
    )
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["task"] == "demand"
    assert rows[0]["model"] == "ridge"
    assert rows[0]["metric"] == "MAE"
    assert rows[0]["cv_mean"] == "12.3"
    assert rows[0]["data_source"] == "synthetic"


def test_log_result_is_append_only(tmp_path: Path) -> None:
    path = tmp_path / "results.csv"
    for model in ("ridge", "xgboost", "hgbr"):
        log_result(
            ExperimentResult(
                task="demand", model=model, metric="MAE", cv_mean=1.0, cv_std=0.1, n_splits=5
            ),
            path=path,
        )
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # Three candidates logged for one task — the constitution's ">=3 models" record.
    assert [r["model"] for r in rows] == ["ridge", "xgboost", "hgbr"]
