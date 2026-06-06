"""Task 6.14 — CI guards: the ML layer stays OFFLINE by default.

The constitution's serving rule (and the whole stub-by-default design) hinges on one
invariant: a fresh process — CI, a new dev box — must never reach for a trained artifact or
the network. This pins it so a future change that flips the default (or makes a factory load
eagerly) turns the suite red:

  - Settings.ml_mode defaults to "stub" (no ML_MODE env set in CI);
  - the build_* factories with default settings return the offline stubs (no joblib, no
    artifact needed) — exactly like ocr_mode=stub / embedding_mode=stub.

The leakage test (test_features.py::test_demand_earlier_as_of_sees_strictly_less) and the
golden-eval gate (python -m app.ml.eval, a CI step) live in their own files; together with
this guard they are the Phase 6 CI safety net.
"""

from app.infra.settings import Settings
from app.ml.predictors import (
    StubAnomalyDetector,
    StubChurnPredictor,
    StubDemandPredictor,
    build_anomaly_detector,
    build_churn_predictor,
    build_demand_predictor,
)


def _settings(**overrides) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://x:x@localhost/x",
        "redis_url": "redis://localhost:6379",
        "vault_addr": "http://localhost:8200",
        "vault_token": "root",
    }
    base.update(overrides)
    return Settings(**base)


def test_ml_mode_defaults_to_stub() -> None:
    # The default everywhere unless explicitly set to "trained" — CI/dev stays offline.
    assert _settings().ml_mode == "stub"


def test_default_settings_build_offline_stubs() -> None:
    settings = _settings()
    assert isinstance(build_demand_predictor(settings), StubDemandPredictor)
    assert isinstance(build_churn_predictor(settings), StubChurnPredictor)
    assert isinstance(build_anomaly_detector(settings), StubAnomalyDetector)
