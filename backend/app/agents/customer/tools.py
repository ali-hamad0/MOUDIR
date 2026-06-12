"""CustomerAgent tools: get_churn_risks, draft_reengagement, queue_reengagement.

Constitution IV: ChurnPredictor ranks the at-risk customers (no LLM). The LLM ONLY
drafts the re-engagement message text — it never decides who is at risk.

Constitution V: queue_reengagement writes a draft to pending_reengagements (status
"draft"). The owner must approve from the dashboard before anything is sent. There
is NO tool path to a WhatsApp send in Phase 7 — that is Phase 10 (Meta API).

All tools are tenant-scoped through ToolContext — The Wall holds at the tool boundary.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.customer.schemas import ChurnRisks, CustomerRisk, DraftMessage, QueuedAction
from app.agents.llm.router import LLMRouter
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import ChurnPredictor, StubChurnPredictor
from app.repositories.customers import CustomerRepository
from app.repositories.pending_reengagements import PendingReengagementRepository
from app.repositories.training_data import TrainingDataRepository
from prompts import customer_agent_ar

log = get_logger(__name__)

RISK_THRESHOLD = 0.5  # score > this → counted as "at risk" in total_at_risk
DEFAULT_TOP_N = 3


@dataclass
class ToolContext:
    """Everything a customer tool needs, bound to one tenant.

    `churn_predictor` is the lifespan-loaded ML model (or offline stub in CI/dev).
    Keeping it here means the same tool code runs in production and tests.
    """

    session: AsyncSession
    tenant_id: UUID
    router: LLMRouter
    settings: Settings
    churn_predictor: ChurnPredictor = field(default_factory=StubChurnPredictor)


async def get_churn_risks(ctx: ToolContext, *, top_n: int = DEFAULT_TOP_N) -> ChurnRisks:
    """Fetch customer order history and call the ML ChurnPredictor. No LLM.

    Constitution IV: the predictor decides who is at risk. The Wall: the order
    fetch and the customer name fetch are both scoped to ctx.tenant_id.
    """
    orders = await TrainingDataRepository(ctx.session).customer_orders(ctx.tenant_id)
    risk_scores: dict[str, float] = ctx.churn_predictor.predict_risks(ctx.tenant_id, orders)

    if not risk_scores:
        log.info("tool.get_churn_risks", tenant_id=str(ctx.tenant_id), at_risk=0)
        return ChurnRisks(customers=[], total_at_risk=0)

    # Derive last order date per customer from the per-order history (Beirut day)
    last_by_customer: dict[str, date | None] = {}
    for row in orders:
        cid_str = str(row[0])
        day: date = row[1]
        prev = last_by_customer.get(cid_str)
        if prev is None or day > prev:
            last_by_customer[cid_str] = day

    sorted_entries = sorted(risk_scores.items(), key=lambda kv: kv[1], reverse=True)
    top_entries = sorted_entries[:top_n]
    total_at_risk = sum(1 for s in risk_scores.values() if s > RISK_THRESHOLD)

    customer_repo = CustomerRepository(ctx.session)
    at_risk: list[CustomerRisk] = []
    for cid_str, score in top_entries:
        try:
            cid = UUID(cid_str)
        except ValueError:
            continue
        customer = await customer_repo.get(ctx.tenant_id, cid)
        if customer is None:
            continue
        at_risk.append(
            CustomerRisk(
                customer_id=cid,
                customer_name_ar=customer.display_name,
                phone_number=customer.phone_number,
                risk_score=score,
                last_order_at=last_by_customer.get(cid_str),
            )
        )

    log.info(
        "tool.get_churn_risks",
        tenant_id=str(ctx.tenant_id),
        at_risk=total_at_risk,
        top_n=len(at_risk),
    )
    return ChurnRisks(customers=at_risk, total_at_risk=total_at_risk)


async def draft_reengagement(ctx: ToolContext, customer: CustomerRisk) -> str:
    """Draft a warm Lebanese Arabic re-engagement WhatsApp message. LLM Tier 1.

    Constitution IV: the LLM drafts the message — it does NOT decide who is at risk.
    Output is Pydantic-validated; bad output retries, then falls back to a template
    so a draft is always producible regardless of LLM availability.
    """
    name = customer.customer_name_ar or "حبيبنا"
    if customer.last_order_at is not None:
        days_ago = (date.today() - customer.last_order_at).days
        last_order_info = f"آخر طلب كان قبل {days_ago} يوم"
    else:
        last_order_info = "ما عندي تفاصيل عن آخر طلب"

    system = customer_agent_ar.DRAFT_SYSTEM.format(
        customer_name=name,
        last_order_info=last_order_info,
    )
    model = ctx.router.tier1().with_structured_output(DraftMessage)
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=customer_agent_ar.DRAFT_HUMAN),
    ]

    attempts = ctx.settings.llm_max_retries + 1
    for attempt in range(attempts):
        try:
            result: DraftMessage = await model.ainvoke(messages)
        except (ValidationError, ValueError) as e:
            log.warning(
                "tool.draft_reengagement.invalid",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error=str(e),
            )
            continue
        except Exception as e:
            log.warning(
                "tool.draft_reengagement.llm_error",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error_type=type(e).__name__,
            )
            break
        log.info("tool.draft_reengagement.ok", tenant_id=str(ctx.tenant_id), attempt=attempt + 1)
        return result.message_ar

    log.info("tool.draft_reengagement.fallback", tenant_id=str(ctx.tenant_id))
    return customer_agent_ar.FALLBACK_DRAFT.format(customer_name=name)


async def queue_reengagement(
    ctx: ToolContext, customer: CustomerRisk, draft_message_ar: str
) -> QueuedAction:
    """Queue a re-engagement draft for HIL approval. Constitution V — NO send.

    Idempotent: if a draft already exists for this customer (status "draft"), returns
    the existing one so the owner does not see duplicate approval requests. The
    action_key `"send_reengagement:{tenant_id}:{customer_id}"` is the idempotency key.

    Flushes (gets an ID) but does NOT commit — the caller (`CustomerAgent.handle`)
    commits once after the graph completes, keeping the write a single unit of work.
    """
    action_key = f"send_reengagement:{ctx.tenant_id}:{customer.customer_id}"
    repo = PendingReengagementRepository(ctx.session)

    existing = await repo.find_draft_for_customer(ctx.tenant_id, customer.customer_id)
    if existing is not None:
        log.info(
            "tool.queue_reengagement.already_queued",
            tenant_id=str(ctx.tenant_id),
            customer_id=str(customer.customer_id),
            reengagement_id=str(existing.id),
        )
        return QueuedAction(
            reengagement_id=existing.id,
            customer_id=customer.customer_id,
            customer_name_ar=customer.customer_name_ar,
            draft_message_ar=existing.draft_message_ar,
            action_key=existing.action_key,
        )

    record = await repo.create(
        ctx.tenant_id,
        customer_id=customer.customer_id,
        customer_name_ar=customer.customer_name_ar,
        draft_message_ar=draft_message_ar,
        action_key=action_key,
    )
    log.info(
        "tool.queue_reengagement",
        tenant_id=str(ctx.tenant_id),
        customer_id=str(customer.customer_id),
        reengagement_id=str(record.id),
    )
    return QueuedAction(
        reengagement_id=record.id,
        customer_id=customer.customer_id,
        customer_name_ar=customer.customer_name_ar,
        draft_message_ar=draft_message_ar,
        action_key=action_key,
    )


def _build_owner_reply(churn_risks: ChurnRisks | None, queued_action: QueuedAction | None) -> str:
    """Build the Lebanese Arabic reply to the owner. Deterministic — no LLM."""
    if not churn_risks or not churn_risks.customers:
        return customer_agent_ar.REPLY_NO_RISKS

    lines = [customer_agent_ar.REPLY_RISKS_HEADER.format(count=churn_risks.total_at_risk)]
    for c in churn_risks.customers:
        name = c.customer_name_ar or c.phone_number
        lines.append(customer_agent_ar.REPLY_RISK_LINE.format(name=name, score=c.risk_score))

    if queued_action is not None:
        name = queued_action.customer_name_ar or "الزبون"
        lines.append(customer_agent_ar.REPLY_ACTION_QUEUED.format(customer_name=name))

    return "\n".join(lines)
