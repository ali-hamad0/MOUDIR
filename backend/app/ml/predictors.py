"""The prediction seam (AD-6.3). Built ONCE, served through DI — never in a route.

Mirrors the Phase 5 infra seams (OCREngine / EmbeddingClient): callers depend on a
small Protocol, and a factory selects the implementation from `Settings.ml_mode`:

- `"stub"`  — deterministic, offline, no artifact needed. The CI/dev DEFAULT, so the
  test suite stays offline and needs no trained `.joblib` committed to run — exactly
  like `ocr_mode=stub` / `embedding_mode=stub`.
- `"trained"` — load the real `*.joblib` from `app/ml/artifacts/` (filled in by the
  per-model serve tasks 6.6/6.8/6.9). If `ml_mode="trained"` but an artifact is
  missing, we LOG and fall back to the stub rather than crash startup: a missing model
  must degrade to the documented default, never take the api down (Constitution IV's
  "brand-new product with no history" behavior, generalized).

This module holds the Protocols and the factory shells for Task 6.1 (the scaffold).
The concrete trained predictors live in `app/ml/<model>/predictor.py` and are wired
into these factories by their serve tasks. Until then the factories return stubs.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


# ── Protocols (the only thing callers depend on) ─────────────────────────────


class DemandPredictor(Protocol):
    """Per-product demand forecast. Sync (CPU, one row, fast — AD-6.5) so it can sit
    behind the existing sync `forecast_demand(ctx, inventory) -> int` unchanged."""

    def predict_quantity(
        self,
        tenant_id: UUID,
        product_id: UUID,
        history: Sequence,
        *,
        as_of: date | None = None,
    ) -> int | None:
        """Predicted demand (units) for a product, or None when there is no signal
        (no history / no model) — the caller then uses its documented fallback.

        `history` is this tenant's per-product daily demand (the `daily_product_demand`
        read shape); the caller fetches it tenant-scoped and passes it in, keeping the
        prediction inside the Wall. `as_of` (default: the day after the last history day)
        is the day being forecast."""
        ...


class ChurnPredictor(Protocol):
    """Per-customer churn risk (binary). Returns a probability in [0, 1]."""

    def predict_risk(self, tenant_id: UUID, customer_id: UUID) -> float | None: ...


class AnomalyDetector(Protocol):
    """Is a given day's revenue anomalous for this tenant? Unsupervised + threshold."""

    def is_anomalous(self, tenant_id: UUID, revenue: float) -> bool | None: ...


# ── Stubs (deterministic, offline — the CI/dev default) ──────────────────────


class StubDemandPredictor:
    """Returns None so every caller falls back to its documented default. The trained
    DemandPredictor (Task 6.6) replaces this when `ml_mode="trained"` and an artifact
    is present. Deterministic and offline — safe for CI."""

    def predict_quantity(
        self,
        tenant_id: UUID,
        product_id: UUID,
        history: Sequence,
        *,
        as_of: date | None = None,
    ) -> int | None:
        return None


class StubChurnPredictor:
    """Returns None (no signal) for CI/dev. Real model in Task 6.8."""

    def predict_risk(self, tenant_id: UUID, customer_id: UUID) -> float | None:
        return None


class StubAnomalyDetector:
    """Returns None (no signal) for CI/dev. Real model in Task 6.9."""

    def is_anomalous(self, tenant_id: UUID, revenue: float) -> bool | None:
        return None


# ── Factories (selected by Settings.ml_mode) ─────────────────────────────────


def _artifact_present(name: str) -> bool:
    return (ARTIFACTS_DIR / name).exists()


def _card_path(artifact_name: str) -> Path:
    """The model card written beside an artifact (`demand.joblib` → `demand_card.json`),
    matching the trainer's naming (app/ml/demand/train.py)."""
    return ARTIFACTS_DIR / f"{Path(artifact_name).stem}_card.json"


def build_demand_predictor(settings: Settings) -> DemandPredictor:
    """Real trained predictor when `ml_mode="trained"` and BOTH the artifact and its card
    exist (the card carries the feature list); otherwise the offline stub. A missing
    artifact degrades to the stub (logged), never crashes startup (AD-6.3)."""
    if settings.ml_mode == "trained":
        artifact = ARTIFACTS_DIR / settings.ml_demand_artifact
        card = _card_path(settings.ml_demand_artifact)
        if artifact.exists() and card.exists():
            # Imported lazily so the stub-default path (CI/dev) never pulls in joblib/the
            # pipeline modules just to import the seam.
            from app.ml.demand.predictor import TrainedDemandPredictor

            log.info("ml.predictor.demand.trained", artifact=settings.ml_demand_artifact)
            return TrainedDemandPredictor.load(artifact, card)
        log.warning(
            "ml.predictor.demand.artifact_missing",
            artifact=settings.ml_demand_artifact,
            fallback="stub",
        )
    return StubDemandPredictor()


def build_churn_predictor(settings: Settings) -> ChurnPredictor:
    """Real trained predictor when available; otherwise the offline stub.
    Task 6.8 fills in the trained branch."""
    if settings.ml_mode == "trained" and not _artifact_present(settings.ml_churn_artifact):
        log.warning(
            "ml.predictor.churn.artifact_missing",
            artifact=settings.ml_churn_artifact,
            fallback="stub",
        )
    return StubChurnPredictor()


def build_anomaly_detector(settings: Settings) -> AnomalyDetector:
    """Real trained detector when available; otherwise the offline stub.
    Task 6.9 fills in the trained branch."""
    if settings.ml_mode == "trained" and not _artifact_present(settings.ml_anomaly_artifact):
        log.warning(
            "ml.predictor.anomaly.artifact_missing",
            artifact=settings.ml_anomaly_artifact,
            fallback="stub",
        )
    return StubAnomalyDetector()


__all__ = [
    "AnomalyDetector",
    "ChurnPredictor",
    "DemandPredictor",
    "StubAnomalyDetector",
    "StubChurnPredictor",
    "StubDemandPredictor",
    "build_anomaly_detector",
    "build_churn_predictor",
    "build_demand_predictor",
]
