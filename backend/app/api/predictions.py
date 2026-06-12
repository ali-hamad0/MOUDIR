"""Read-only ML predictions API (Phase 6, Task 6.10).

GET /predictions/demand | /churn | /anomaly. Every endpoint:
  - is behind auth (get_current_user) and scoped to the JWT tenant — there is NO tenant_id
    in any request, so nothing to spoof (the Wall);
  - reads the tenant's history through the tenant-scoped TrainingDataRepository, builds the
    same leakage-free features the trainers used, and serves predictions from the
    lifespan-loaded predictor reached via `request.app.state` — the model is loaded ONCE at
    startup, NEVER in the handler (Constitution IV.5);
  - is purely READ-THROUGH: nothing is persisted (decision for 6.10 — these are deterministic
    recomputes, so a `prediction_runs` audit table buys nothing; recorded in DECISIONS).
    There is NO new HIL action here — predictions are shown, never acted on (that's Phase 7).

With `ml_mode="stub"` (CI/dev default) the predictors return no signal, so values are null
but the tenant's entities are still enumerated — which is exactly what the cross-tenant
isolation test checks.
"""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.predictions import (
    AnomalyDay,
    AnomalyPredictions,
    ChurnPrediction,
    ChurnPredictions,
    DemandPrediction,
    DemandPredictions,
)
from app.db.models import User
from app.db.session import get_db_session
from app.ml.predictors import StubAnomalyDetector, StubChurnPredictor, StubDemandPredictor
from app.repositories.training_data import TrainingDataRepository
from app.services.plan_gate import require_pro

router = APIRouter(prefix="/predictions", tags=["predictions"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


def _mode(predictor: object, stub_type: type) -> str:
    """Honesty marker: did we serve the real model or fall back to the offline stub?"""
    return "stub" if isinstance(predictor, stub_type) else "trained"


@router.get("/demand", response_model=DemandPredictions)
async def predict_demand(user: CurrentUser, db: Db, request: Request) -> DemandPredictions:
    """Next-day demand forecast per product that has sales history, for this tenant only."""
    await require_pro(db, user.tenant_id, "insights")  # ML insights are a Pro feature
    rows = await TrainingDataRepository(db).daily_product_demand(user.tenant_id)
    predictor = request.app.state.demand_predictor

    # Distinct products in the order they first appear; the as_of header is the day after
    # the latest known sale across the catalog.
    product_ids: list = []
    seen: set = set()
    for product_id, *_ in rows:
        if product_id not in seen:
            seen.add(product_id)
            product_ids.append(product_id)
    as_of = (max(day for _, day, _ in rows) + timedelta(days=1)) if rows else date.today()

    items = [
        DemandPrediction(
            product_id=product_id,
            predicted_units=predictor.predict_quantity(user.tenant_id, product_id, rows),
        )
        for product_id in product_ids
    ]
    return DemandPredictions(as_of=as_of, mode=_mode(predictor, StubDemandPredictor), items=items)


@router.get("/churn", response_model=ChurnPredictions)
async def predict_churn(user: CurrentUser, db: Db, request: Request) -> ChurnPredictions:
    """Churn risk per returning customer (customers with a past order), this tenant only."""
    await require_pro(db, user.tenant_id, "insights")
    orders = await TrainingDataRepository(db).customer_orders(user.tenant_id)
    predictor = request.app.state.churn_predictor
    risks = predictor.predict_risks(user.tenant_id, orders)

    days = [day for _, day, _ in orders]
    as_of = (max(days) + timedelta(days=1)) if days else date.today()

    customer_ids: list = []
    seen: set = set()
    for customer_id, *_ in orders:
        if customer_id not in seen:
            seen.add(customer_id)
            customer_ids.append(customer_id)
    items = [
        ChurnPrediction(customer_id=customer_id, risk=risks.get(str(customer_id)))
        for customer_id in customer_ids
    ]
    return ChurnPredictions(as_of=as_of, mode=_mode(predictor, StubChurnPredictor), items=items)


@router.get("/anomaly", response_model=AnomalyPredictions)
async def predict_anomaly(
    user: CurrentUser,
    db: Db,
    request: Request,
    window: Annotated[int, Query(ge=1, le=90)] = 14,
) -> AnomalyPredictions:
    """Whether each of the last `window` revenue days is anomalous, this tenant only."""
    await require_pro(db, user.tenant_id, "insights")
    rows = await TrainingDataRepository(db).daily_revenue(user.tenant_id)
    predictor = request.app.state.anomaly_detector
    flags = predictor.flag_days(user.tenant_id, rows, window=window)

    recent = rows[-window:]
    items = [
        AnomalyDay(day=day, revenue_lbp=int(total_lbp), is_anomalous=flags.get(day))
        for day, total_lbp in recent
    ]
    as_of = items[-1].day if items else date.today()
    return AnomalyPredictions(as_of=as_of, mode=_mode(predictor, StubAnomalyDetector), items=items)
