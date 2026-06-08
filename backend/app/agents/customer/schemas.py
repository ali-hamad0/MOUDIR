"""Data contracts for the CustomerAgent.

Dataclasses carry internal pipeline state; the Pydantic model is the LLM
structured-output contract for the re-engagement draft (validated, retried on failure).
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


@dataclass
class CustomerRisk:
    customer_id: UUID
    customer_name_ar: str | None  # display_name from customers table (may be null)
    phone_number: str
    risk_score: float  # 0–1 from ChurnPredictor; higher = more at risk
    last_order_at: date | None  # most recent Beirut-day order (from customer_orders)


@dataclass
class ChurnRisks:
    customers: list[CustomerRisk]  # top-N sorted by risk_score DESC
    total_at_risk: int  # all customers above the risk threshold (> RISK_THRESHOLD)


class DraftMessage(BaseModel):
    """LLM-structured re-engagement message for one customer.

    A single non-empty Lebanese Arabic WhatsApp message. The model is told to keep
    it warm and brief; the customer's name and last-order info are injected so the
    LLM only composes the text — it does not decide who is at risk (Constitution IV).
    """

    message_ar: str = Field(min_length=1)


@dataclass
class QueuedAction:
    reengagement_id: UUID  # PendingReengagement.id
    customer_id: UUID
    customer_name_ar: str | None
    draft_message_ar: str
    action_key: str  # "send_reengagement:{tenant_id}:{customer_id}" — idempotency key
