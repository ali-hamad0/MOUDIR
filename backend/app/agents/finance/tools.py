"""FinanceAgent tools: get_revenue_summary, flag_anomalies, compose_reply.

Constitution IV is strictly enforced here:
  - get_revenue_summary reads raw revenue from Postgres (no LLM).
  - flag_anomalies calls the ML AnomalyDetector (no LLM).
  - compose_reply receives pre-flagged data from the detector and uses the LLM
    ONLY to explain the results in Lebanese Arabic — it never asks the LLM to
    decide whether a day is anomalous.

All three tools are tenant-scoped through ToolContext — The Wall holds at the
tool boundary.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from langchain_core.messages import SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.finance.schemas import AnomalyResult, FinanceReply, RevenueDay, RevenueSummary
from app.agents.llm.router import LLMRouter
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import AnomalyDetector, StubAnomalyDetector
from app.repositories.training_data import TrainingDataRepository
from prompts import finance_agent_ar

log = get_logger(__name__)


@dataclass
class ToolContext:
    """Everything a finance tool needs, bound to one tenant.

    `anomaly_detector` is the lifespan-loaded ML model (or the offline stub in CI/dev).
    Keeping it here means the same tool code runs in production and tests — only the
    injected detector changes.
    """

    session: AsyncSession
    tenant_id: UUID
    router: LLMRouter
    settings: Settings
    anomaly_detector: AnomalyDetector = field(default_factory=StubAnomalyDetector)


def _compute_trend(days: list[RevenueDay]) -> str:
    """Compare last 7 days' avg revenue to the prior 7 days'.

    Returns "up", "down", or "stable". Needs ≥ 14 data points; fewer → "stable".
    """
    if len(days) < 14:
        return "stable"
    recent_avg = sum(d.revenue_lbp for d in days[-7:]) / 7
    prior_avg = sum(d.revenue_lbp for d in days[-14:-7]) / 7
    if prior_avg == 0:
        return "stable"
    ratio = recent_avg / prior_avg
    if ratio > 1.10:
        return "up"
    if ratio < 0.90:
        return "down"
    return "stable"


def _build_anomaly_section(anomaly_result: AnomalyResult) -> str:
    """Format the anomaly block that is injected into the LLM prompt."""
    if anomaly_result.anomaly_count == 0:
        return finance_agent_ar.NO_ANOMALY
    header = finance_agent_ar.ANOMALY_HEADER.format(anomaly_count=anomaly_result.anomaly_count)
    lines = [
        finance_agent_ar.ANOMALY_LINE.format(day=d.day, revenue_lbp=d.revenue_lbp)
        for d in anomaly_result.flagged_days
    ]
    return header + "\n" + "\n".join(lines)


async def get_revenue_summary(
    ctx: ToolContext, *, days: int = 30
) -> tuple[Sequence, RevenueSummary]:
    """Fetch the last `days` days of daily revenue for this tenant. No LLM.

    Returns the raw rows (passed to the anomaly detector unchanged) and a
    RevenueSummary (passed to the compose step). Tenant-scoped via ctx.tenant_id —
    The Wall holds at the repository level.
    """
    end = date.today()
    start = end - timedelta(days=days)
    rows = await TrainingDataRepository(ctx.session).daily_revenue(
        ctx.tenant_id, start=start, end=end
    )
    revenue_days = [RevenueDay(day=r[0], revenue_lbp=int(r[1])) for r in rows]
    total_lbp = sum(d.revenue_lbp for d in revenue_days)
    daily_avg = total_lbp // days if days > 0 else 0
    trend = _compute_trend(revenue_days)
    summary = RevenueSummary(
        days_window=days,
        total_lbp=total_lbp,
        daily_avg_lbp=daily_avg,
        trend=trend,
        days=revenue_days,
    )
    log.info(
        "tool.get_revenue_summary",
        tenant_id=str(ctx.tenant_id),
        days=days,
        total_lbp=total_lbp,
        trend=trend,
    )
    return rows, summary


def flag_anomalies(ctx: ToolContext, revenue_rows: Sequence) -> AnomalyResult:
    """Apply the ML anomaly detector. No LLM.

    Constitution IV: the trained AnomalyDetector decides which days are anomalous.
    The compose_reply step then uses the LLM to EXPLAIN those flags — it does not
    produce them. The Wall holds: the detector is called with ctx.tenant_id.
    """
    flagged: dict[date, bool] = ctx.anomaly_detector.flag_days(ctx.tenant_id, revenue_rows)
    revenue_by_day: dict[date, int] = {r[0]: int(r[1]) for r in revenue_rows}
    flagged_days = [
        RevenueDay(day=d, revenue_lbp=revenue_by_day.get(d, 0))
        for d, is_flag in flagged.items()
        if is_flag
    ]
    result = AnomalyResult(
        flagged_days=flagged_days,
        total_days=len(revenue_rows),
        anomaly_count=len(flagged_days),
    )
    log.info(
        "tool.flag_anomalies",
        tenant_id=str(ctx.tenant_id),
        total_days=result.total_days,
        anomaly_count=result.anomaly_count,
    )
    return result


async def compose_reply(
    ctx: ToolContext,
    *,
    question: str,
    summary: RevenueSummary,
    anomaly_result: AnomalyResult,
) -> str:
    """Explain the revenue and anomaly flags in Lebanese Arabic. LLM ONLY explains.

    The LLM receives pre-flagged results from the ML detector and pre-computed
    summary stats — it is never asked to flag or compute anything itself
    (Constitution IV). Output is Pydantic-validated; bad output retries, then
    falls back to a templated message so a reply is always returned.
    """
    anomaly_section = _build_anomaly_section(anomaly_result)
    trend_label = finance_agent_ar.TREND_LABELS.get(summary.trend, summary.trend)
    system = finance_agent_ar.COMPOSE_REPLY_SYSTEM.format(
        question=question,
        days_window=summary.days_window,
        total_lbp=summary.total_lbp,
        daily_avg_lbp=summary.daily_avg_lbp,
        trend_label=trend_label,
        anomaly_section=anomaly_section,
    )
    model = ctx.router.tier1().with_structured_output(FinanceReply)
    messages = [SystemMessage(content=system)]

    attempts = ctx.settings.llm_max_retries + 1
    for attempt in range(attempts):
        try:
            result: FinanceReply = await model.ainvoke(messages)
        except (ValidationError, ValueError) as e:
            log.warning(
                "tool.compose_reply.invalid",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error=str(e),
            )
            continue
        except Exception as e:
            log.warning(
                "tool.compose_reply.llm_error",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error_type=type(e).__name__,
            )
            break
        log.info("tool.compose_reply.ok", tenant_id=str(ctx.tenant_id), attempt=attempt + 1)
        return result.reply_ar

    log.info("tool.compose_reply.fallback", tenant_id=str(ctx.tenant_id))
    return finance_agent_ar.FALLBACK_REPLY
