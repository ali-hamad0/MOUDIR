"""Trained demand predictor — the serving side of the seam (Phase 6, Task 6.6).

Wraps the `demand.joblib` sklearn.Pipeline that Task 6.5 trained and exposes the sync
`predict_quantity(...)` the DemandPredictor Protocol (app/ml/predictors.py) defines. Built
ONCE at startup (lifespan → app.state.demand_predictor) and reached via DI — never loaded
in a route (Constitution IV.5).

How a prediction is made (no leakage, same builder as training — AD-6.6):
  - the caller hands us this tenant's per-product daily demand history (already inside the
    Wall — we never read the DB here);
  - we keep only the rows for the asked product, then build features for ONE future day
    (`as_of`, default = the day after the last history day) with the SAME
    `app.ml.features.demand.build_demand_features` the trainer used, so the predictor and
    the model can never disagree on columns;
  - the feature LIST itself comes from the model card (not hard-coded), so a retrain that
    changes the columns is picked up automatically;
  - we run the pipeline on that single row and return the rounded, non-negative units.

Cold start (a brand-new product with no history) returns None, so the caller falls back to
its documented default (AD-6.5) — the model is never asked to hallucinate a number.

Kept sync (AD-6.5): joblib/sklearn `predict` on one row is CPU-bound and fast, so it sits
behind the existing sync `forecast_demand(ctx, inventory) -> int` unchanged.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import joblib

from app.infra.logging import get_logger
from app.ml.features.demand import DemandRow, build_demand_features
from app.ml.features.demand import _to_rows as to_demand_rows
from app.ml.model_card import ModelCard

log = get_logger(__name__)


class TrainedDemandPredictor:
    """Real DemandPredictor backed by the trained pipeline + its model card."""

    def __init__(self, pipeline, features: list[str]) -> None:
        self._pipeline = pipeline
        self._features = features

    @classmethod
    def load(cls, artifact_path: Path, card_path: Path) -> "TrainedDemandPredictor":
        """Load the pipeline and read its feature list from the card. Called ONCE from the
        factory (which runs in lifespan); the loky/joblib core-count warning on Windows is
        harmless (Linux CI is clean)."""
        pipeline = joblib.load(artifact_path)
        card = ModelCard.load(card_path)
        log.info(
            "ml.predictor.demand.loaded",
            model=card.model,
            n_features=len(card.features),
            trained_at=card.trained_at,
        )
        return cls(pipeline, card.features)

    def predict_quantity(
        self,
        tenant_id: UUID,
        product_id: UUID,
        history: Sequence,
        *,
        as_of: date | None = None,
    ) -> int | None:
        """Predicted demand (units) for `product_id` on `as_of` (default: the day after the
        last history day). `history` is this tenant's per-product daily demand — the
        `daily_product_demand` read shape (DemandRow or (product_id, day, units) rows).
        Returns None when there is no usable history (cold start), so the caller falls back
        to its documented default."""
        rows = [r for r in to_demand_rows(history) if r.product_id == str(product_id)]
        if not rows:
            return None

        last_day = max(r.day for r in rows)
        pred_day = as_of if as_of is not None else last_day + timedelta(days=1)

        # A placeholder observation lets the builder emit a feature row for a FUTURE day;
        # its units value is never an input (lags/rollings are shifted strictly before the
        # row, the calendar features are knowable for pred_day) — only the feature columns
        # are read. For a historical/gap pred_day the dense daily calendar already covers
        # it, so no placeholder is needed (and appending one could shadow a real value).
        records: list[DemandRow] = list(rows)
        if pred_day > last_day:
            records.append(DemandRow(product_id=str(product_id), day=pred_day, units=0))

        feats = build_demand_features(records)
        target = feats[feats["day"] == pred_day]
        if target.empty:
            return None

        predicted = float(self._pipeline.predict(target[self._features])[0])
        qty = max(0, round(predicted))
        log.info(
            "ml.predictor.demand.predict",
            tenant_id=str(tenant_id),
            product_id=str(product_id),
            as_of=str(pred_day),
            qty=qty,
        )
        return qty
