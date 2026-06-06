"""Response schemas for the read-only /predictions/* API (Phase 6, Task 6.10).

Every prediction is for ONE tenant (the JWT's) — there is no tenant_id in any request or
response; scope comes from the authenticated user. `as_of` makes each response reproducible
(the cutoff/day the numbers were computed for). A null value (predicted_units / risk /
is_anomalous) means "no signal" — the offline stub, or a cold-start entity — never an error.
"""

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class DemandPrediction(BaseModel):
    product_id: UUID
    predicted_units: int | None  # next-day forecast; null when the product has no history


class DemandPredictions(BaseModel):
    as_of: date  # the day being forecast (day after the last known sale)
    mode: str  # "trained" | "stub" — honesty marker, mirrors ocr_mode/embedding_mode
    items: list[DemandPrediction]


class ChurnPrediction(BaseModel):
    customer_id: UUID
    risk: float | None  # P(churn) in [0, 1]; null when the customer has no history


class ChurnPredictions(BaseModel):
    as_of: date  # the cutoff the risk is computed at
    mode: str
    items: list[ChurnPrediction]


class AnomalyDay(BaseModel):
    day: date
    revenue_lbp: int
    is_anomalous: bool | None  # null when there is no model/signal for that day


class AnomalyPredictions(BaseModel):
    as_of: date  # the most recent day in the window
    mode: str
    items: list[AnomalyDay]
