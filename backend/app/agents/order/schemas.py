"""Pydantic contracts for the order flow, shared by the agent tools (Task 2.8)
and the order-writing service (Task 2.5).

`ParsedOrder` is what `parse_order` must emit — the LLM's output is validated
into this shape; bad output triggers a retry, not a crash (constitution). The
agent never hands the service anything but a validated `ConfirmedOrder`, and the
service still re-checks every line against the live catalog before writing.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

FulfillmentType = Literal["pickup", "delivery"]


class ParsedOrderItem(BaseModel):
    """One requested line, as extracted from the customer's message. `product_id`
    is filled by matching against the catalog returned by `get_products` — the
    LLM is constrained to that catalog, it does not invent ids."""

    product_id: UUID
    quantity: int = Field(ge=1)


class ParsedOrder(BaseModel):
    """The structured result of parsing a Lebanese-Arabic order message."""

    items: list[ParsedOrderItem] = Field(min_length=1)
    fulfillment_type: FulfillmentType = "pickup"
    # The customer's raw Arabic time phrase ("بكرا الصبح"), kept verbatim.
    requested_time_text: str | None = None
    note: str | None = None


# `ConfirmedOrder` is the validated payload the service accepts. It is the same
# shape as `ParsedOrder` in Phase 2 (kept distinct so confirm-time fields — e.g.
# an approval token in later phases — can diverge without touching parsing).
class ConfirmedOrder(ParsedOrder):
    pass
