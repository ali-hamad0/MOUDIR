"""AdvisorAgent tools: snapshot, demand forecast, churn summary, anomaly status, briefing.

Constitution IV (all five tools):
- get_todays_snapshot: pure DB reads, no ML, no LLM.
- get_demand_forecast: DemandPredictor decides quantity. LLM absent.
- get_churn_summary: ChurnPredictor ranks customers. LLM absent.
- get_anomaly_status: AnomalyDetector flags days. LLM absent.
- compose_briefing: receives the four pre-computed dataclasses; LLM ONLY synthesizes.
  It does not produce, decide, or recalculate any number.

The Wall: every DB read passes ctx.tenant_id to tenant-scoped repositories.
No cross-tenant join or raw SQL string. The predictor calls also receive tenant_id
so the ML model can scope its feature slice — same contract as Phase 6.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.advisor.schemas import (
    AnomalyStatus,
    Briefing,
    ChurnSummary,
    DemandForecast,
    DemandForecastItem,
    ProductRevenue,
    TodaySnapshot,
)
from app.agents.llm.router import LLMRouter
from app.db.models import Inventory, Order, OrderItem, Product
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import (
    AnomalyDetector,
    ChurnPredictor,
    DemandPredictor,
    StubAnomalyDetector,
    StubChurnPredictor,
    StubDemandPredictor,
)
from app.repositories.customers import CustomerRepository
from app.repositories.orders import OrderRepository
from app.repositories.training_data import TrainingDataRepository
from prompts import advisor_agent_ar

log = get_logger(__name__)

BEIRUT_TZ = ZoneInfo("Asia/Beirut")
CHURN_RISK_THRESHOLD = 0.5


@dataclass
class ToolContext:
    """Everything an advisor tool needs, bound to one tenant.

    All three Phase 6 ML models arrive via DI from lifespan (Constitution IV.5:
    joblib.load lives in lifespan, never in a route or a tool). Keeping all three
    here means the same tool code runs in production and CI (stubs by default).
    """

    session: AsyncSession
    tenant_id: UUID
    router: LLMRouter
    settings: Settings
    demand_predictor: DemandPredictor = field(default_factory=StubDemandPredictor)
    churn_predictor: ChurnPredictor = field(default_factory=StubChurnPredictor)
    anomaly_detector: AnomalyDetector = field(default_factory=StubAnomalyDetector)


def _today_beirut() -> date:
    return datetime.now(BEIRUT_TZ).date()


def _day_start_beirut(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=BEIRUT_TZ)


async def _top_products_today(ctx: ToolContext, *, limit: int = 3) -> list[ProductRevenue]:
    """Top products by line revenue today (Beirut day). Pure DB read, tenant-scoped.

    Uses the snapshotted name_ar_snapshot on OrderItem so historical product names
    are correct even if the catalog changed since the order was placed.
    """
    day_start = _day_start_beirut(_today_beirut())
    revenue_expr = func.coalesce(func.sum(OrderItem.line_total_lbp), 0)
    stmt = (
        select(OrderItem.name_ar_snapshot, revenue_expr.label("rev"))
        .join(Order, (Order.id == OrderItem.order_id) & (Order.tenant_id == OrderItem.tenant_id))
        .where(OrderItem.tenant_id == ctx.tenant_id, Order.created_at >= day_start)
        .group_by(OrderItem.name_ar_snapshot)
        .order_by(revenue_expr.desc())
        .limit(limit)
    )
    rows = (await ctx.session.execute(stmt)).all()
    return [ProductRevenue(name_ar=r[0], revenue_lbp=int(r[1])) for r in rows]


async def _low_stock_with_names(ctx: ToolContext) -> list[tuple[Inventory, str]]:
    """Low-stock Inventory rows joined with Product.name_ar. Tenant-scoped.

    The JOIN is scoped on BOTH sides by tenant_id (constitution I: cross-tenant
    JOIN is a Sev-1 leak). Only rows with a threshold set AND quantity at/below it.
    """
    stmt = (
        select(Inventory, Product.name_ar)
        .join(
            Product,
            (Product.id == Inventory.product_id) & (Product.tenant_id == Inventory.tenant_id),
        )
        .where(
            Inventory.tenant_id == ctx.tenant_id,
            Inventory.reorder_threshold.is_not(None),
            Inventory.quantity <= Inventory.reorder_threshold,
        )
    )
    rows = (await ctx.session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


async def get_todays_snapshot(ctx: ToolContext) -> TodaySnapshot:
    """Today's revenue, 7-day avg, same-day-last-week, order count, top products.

    Pure DB reads — no ML, no LLM. Constitution IV: these numbers are facts;
    the LLM explains them later in compose_briefing.
    The Wall: all reads scoped to ctx.tenant_id.
    """
    today = _today_beirut()
    tr = TrainingDataRepository(ctx.session)

    today_rows = await tr.daily_revenue(ctx.tenant_id, start=today, end=today + timedelta(days=1))
    today_revenue = int(today_rows[0][1]) if today_rows else 0

    week_rows = await tr.daily_revenue(ctx.tenant_id, start=today - timedelta(days=7), end=today)
    avg_7day = int(sum(r[1] for r in week_rows) / len(week_rows)) if week_rows else 0

    last_week_day = today - timedelta(days=7)
    lwk_rows = await tr.daily_revenue(
        ctx.tenant_id, start=last_week_day, end=last_week_day + timedelta(days=1)
    )
    same_day_last_week = int(lwk_rows[0][1]) if lwk_rows else 0

    order_count = await OrderRepository(ctx.session).count_today(ctx.tenant_id)
    top_products = await _top_products_today(ctx)

    log.info(
        "tool.get_todays_snapshot",
        tenant_id=str(ctx.tenant_id),
        today_revenue=today_revenue,
        order_count=order_count,
    )
    return TodaySnapshot(
        today_revenue_lbp=today_revenue,
        avg_7day_lbp=avg_7day,
        same_day_last_week_lbp=same_day_last_week,
        order_count=order_count,
        top_products=top_products,
    )


async def get_demand_forecast(ctx: ToolContext) -> DemandForecast:
    """Demand forecast for all low-stock products. Constitution IV: DemandPredictor decides.

    The Wall: the inventory read and the demand history read are both scoped to
    ctx.tenant_id. The predictor also receives tenant_id so it can scope its
    feature slice if needed.
    """
    low_stock = await _low_stock_with_names(ctx)
    if not low_stock:
        log.info("tool.get_demand_forecast", tenant_id=str(ctx.tenant_id), low_stock=0)
        return DemandForecast(items=[])

    tr = TrainingDataRepository(ctx.session)
    items: list[DemandForecastItem] = []
    for inventory, product_name in low_stock:
        history = await tr.daily_product_demand(ctx.tenant_id, product_id=inventory.product_id)
        predicted = ctx.demand_predictor.predict_quantity(
            ctx.tenant_id, inventory.product_id, history
        )
        items.append(
            DemandForecastItem(
                product_name=product_name,
                current_stock=inventory.quantity,
                predicted_units_7d=predicted if predicted is not None else 0,
            )
        )

    log.info(
        "tool.get_demand_forecast",
        tenant_id=str(ctx.tenant_id),
        low_stock=len(items),
    )
    return DemandForecast(items=items)


async def get_churn_summary(ctx: ToolContext) -> ChurnSummary:
    """Churn risk summary: count at risk + top-3 names. Constitution IV: predictor decides.

    The Wall: customer_orders and the customer name lookup are both scoped to
    ctx.tenant_id. The predictor receives tenant_id for its feature scope.
    """
    orders = await TrainingDataRepository(ctx.session).customer_orders(ctx.tenant_id)
    risk_scores: dict[str, float] = ctx.churn_predictor.predict_risks(ctx.tenant_id, orders)

    if not risk_scores:
        log.info("tool.get_churn_summary", tenant_id=str(ctx.tenant_id), at_risk=0)
        return ChurnSummary(count_at_risk=0, total_customers=0, top_names=[])

    count_at_risk = sum(1 for s in risk_scores.values() if s > CHURN_RISK_THRESHOLD)
    top_entries = sorted(risk_scores.items(), key=lambda kv: kv[1], reverse=True)[:3]

    repo = CustomerRepository(ctx.session)
    names: list[str] = []
    for cid_str, _ in top_entries:
        try:
            cid = UUID(cid_str)
        except ValueError:
            continue
        customer = await repo.get(ctx.tenant_id, cid)
        if customer is not None and customer.display_name:
            names.append(customer.display_name)

    log.info(
        "tool.get_churn_summary",
        tenant_id=str(ctx.tenant_id),
        at_risk=count_at_risk,
        total=len(risk_scores),
    )
    return ChurnSummary(
        count_at_risk=count_at_risk,
        total_customers=len(risk_scores),
        top_names=names,
    )


async def get_anomaly_status(ctx: ToolContext) -> AnomalyStatus:
    """Revenue anomaly status from the Phase 6 detector. Constitution IV: detector decides.

    The Wall: the revenue read is scoped to ctx.tenant_id and the rows are passed
    to the detector (never raw SQL). The detector returns a date→bool map; we
    extract today's flag and the recent list.
    """
    today = _today_beirut()
    revenue_rows = await TrainingDataRepository(ctx.session).daily_revenue(ctx.tenant_id)
    flags: dict[date, bool] = ctx.anomaly_detector.flag_days(ctx.tenant_id, revenue_rows)

    is_today_anomalous = flags.get(today, False)
    recent_flags = sorted(d for d, flagged in flags.items() if flagged)

    log.info(
        "tool.get_anomaly_status",
        tenant_id=str(ctx.tenant_id),
        is_today_anomalous=is_today_anomalous,
        recent_count=len(recent_flags),
    )
    return AnomalyStatus(
        is_today_anomalous=is_today_anomalous,
        recent_flag_count=len(recent_flags),
        recent_flags=recent_flags,
    )


def _format_products(products: list[ProductRevenue]) -> str:
    if not products:
        return advisor_agent_ar.NO_TOP_PRODUCTS
    return "\n".join(
        advisor_agent_ar.PRODUCT_LINE.format(name=p.name_ar, revenue=p.revenue_lbp)
        for p in products
    )


def _format_demand(items: list[DemandForecastItem]) -> str:
    if not items:
        return advisor_agent_ar.NO_DEMAND_ITEMS
    return "\n".join(
        advisor_agent_ar.DEMAND_LINE.format(
            name=item.product_name,
            stock=item.current_stock,
            predicted=item.predicted_units_7d,
        )
        for item in items
    )


async def compose_briefing(
    ctx: ToolContext,
    *,
    snapshot: TodaySnapshot,
    demand: DemandForecast,
    churn: ChurnSummary,
    anomaly: AnomalyStatus,
) -> str:
    """Synthesize all four ML outputs into a Lebanese Arabic morning briefing. LLM Tier 2.

    Constitution IV: the LLM receives pre-computed structured data (from ML models +
    DB reads). It ONLY explains and synthesizes — it does not produce any number,
    flag, or score. The system prompt contains the facts; the LLM writes the narrative.
    Fallback to a static string on LLM error so a briefing is always returnable.
    """
    at_risk_names = (
        advisor_agent_ar.AT_RISK_NAMES_LINE.format(names=", ".join(churn.top_names))
        if churn.top_names
        else advisor_agent_ar.NO_AT_RISK
    )
    system = advisor_agent_ar.BRIEFING_SYSTEM.format(
        today_revenue=snapshot.today_revenue_lbp,
        avg_7day=snapshot.avg_7day_lbp,
        same_day_last_week=snapshot.same_day_last_week_lbp,
        order_count=snapshot.order_count,
        top_products=_format_products(snapshot.top_products),
        demand_items=_format_demand(demand.items),
        at_risk_count=churn.count_at_risk,
        at_risk_names=at_risk_names,
        is_anomalous="نعم ⚠️" if anomaly.is_today_anomalous else "لا",
        anomaly_count=anomaly.recent_flag_count,
    )
    model = ctx.router.tier2().with_structured_output(Briefing)
    messages = [SystemMessage(content=system)]

    attempts = ctx.settings.llm_max_retries + 1
    for attempt in range(attempts):
        try:
            result: Briefing = await model.ainvoke(messages)
        except (ValidationError, ValueError) as e:
            log.warning(
                "tool.compose_briefing.invalid",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error=str(e),
            )
            continue
        except Exception as e:
            log.warning(
                "tool.compose_briefing.llm_error",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error_type=type(e).__name__,
            )
            break
        log.info("tool.compose_briefing.ok", tenant_id=str(ctx.tenant_id), attempt=attempt + 1)
        return result.briefing_ar

    log.info("tool.compose_briefing.fallback", tenant_id=str(ctx.tenant_id))
    return advisor_agent_ar.FALLBACK_BRIEFING
