"""Trained churn predictor — the serving side of the seam (Phase 6, Task 6.10).

Wraps the `churn.joblib` sklearn classification Pipeline (Task 6.8) and scores a tenant's
customers behind the ChurnPredictor Protocol. Built ONCE at startup (lifespan →
app.state.churn_predictor) and reached via DI — never loaded in a route (Constitution IV.5).

How it scores (same builder + feature list as training — no drift):
  - the read API hands us this tenant's per-order history (already inside the Wall);
  - we build the leakage-free RFM features for EVERY customer at one cutoff with the SAME
    app.ml.features.churn.build_churn_features the trainer used (features strictly before the
    cutoff — AD-6.6);
  - the feature LIST comes from the model card, not a hard-coded list;
  - one batched predict_proba returns each customer's churn probability.

`as_of` defaults to the day AFTER the last order (so it scores "who is at risk as of the
end of the known history"), mirroring the demand predictor — meaningful even when the only
data available is older synthetic history.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import joblib

from app.infra.logging import get_logger
from app.ml.features.churn import _to_orders as to_churn_orders
from app.ml.features.churn import build_churn_features
from app.ml.model_card import ModelCard

log = get_logger(__name__)


class TrainedChurnPredictor:
    """Real ChurnPredictor backed by the trained classification pipeline + its card."""

    def __init__(self, pipeline, features: list[str]) -> None:
        self._pipeline = pipeline
        self._features = features

    @classmethod
    def load(cls, artifact_path: Path, card_path: Path) -> "TrainedChurnPredictor":
        pipeline = joblib.load(artifact_path)
        card = ModelCard.load(card_path)
        log.info("ml.predictor.churn.loaded", model=card.model, trained_at=card.trained_at)
        return cls(pipeline, card.features)

    def predict_risks(
        self, tenant_id: UUID, orders: Sequence, *, as_of: date | None = None
    ) -> dict[str, float]:
        """churn probability per customer_id, for every customer with a past order as of
        `as_of` (default: the day after the last order). Empty when there is no history."""
        rows = to_churn_orders(orders)
        if not rows:
            return {}
        cutoff = as_of if as_of is not None else max(o.day for o in rows) + timedelta(days=1)

        features = build_churn_features(rows, as_of=cutoff)
        if features.empty:
            return {}
        # P(churn=1) — column 1 of predict_proba (classes_ is [0, 1]).
        proba = self._pipeline.predict_proba(features[self._features])[:, 1]
        risks = {
            str(customer_id): float(p)
            for customer_id, p in zip(features["customer_id"], proba, strict=True)
        }
        log.info(
            "ml.predictor.churn.scored",
            tenant_id=str(tenant_id),
            as_of=str(cutoff),
            customers=len(risks),
        )
        return risks
