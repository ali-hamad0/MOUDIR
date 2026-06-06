"""Trained anomaly detector — the serving side of the seam (Phase 6, Task 6.10).

Wraps the `anomaly.joblib` SeasonalAnomalyDetector (Task 6.9) and flags a tenant's recent
revenue days behind the AnomalyDetector Protocol. Built ONCE at startup (lifespan →
app.state.anomaly_detector) and reached via DI — never loaded in a route (Constitution IV.5).

The read API hands us this tenant's daily-revenue series (inside the Wall); we rebuild the
seasonality-aware features with the SAME app.ml.features.anomaly builder the trainer used,
flag every day with the saved detector, and return the most recent `window` days. Because a
Ramadan/summer lift is part of the expectation model, those days are NOT flagged just for
being high (the constitution's pitfall).
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from uuid import UUID

import joblib

from app.infra.logging import get_logger
from app.ml.features.anomaly import build_anomaly_features

log = get_logger(__name__)


class TrainedAnomalyDetector:
    """Real AnomalyDetector backed by the trained SeasonalAnomalyDetector artifact."""

    def __init__(self, detector) -> None:
        self._detector = detector

    @classmethod
    def load(cls, artifact_path: Path) -> "TrainedAnomalyDetector":
        detector = joblib.load(artifact_path)
        log.info("ml.predictor.anomaly.loaded", method=getattr(detector, "method", "?"))
        return cls(detector)

    def flag_days(
        self, tenant_id: UUID, revenue_history: Sequence, *, window: int = 14
    ) -> dict[date, bool]:
        """Map of day → is_anomalous for the tenant's revenue series. The FULL series is
        flagged (keyed by day) so the caller can window however it likes and every day it
        renders resolves; `window` is accepted for Protocol parity. Empty with no history."""
        features = build_anomaly_features(revenue_history)
        if features.empty:
            return {}
        flags = self._detector.flag(features)
        result = {day: bool(flag) for day, flag in zip(features["day"], flags, strict=True)}
        log.info(
            "ml.predictor.anomaly.flagged",
            tenant_id=str(tenant_id),
            days=len(result),
            anomalies=sum(result.values()),
        )
        return result
