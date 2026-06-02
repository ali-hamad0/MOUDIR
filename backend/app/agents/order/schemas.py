"""Pydantic contracts for the order flow, shared by the agent tools (Task 2.8)
and the order-writing service (Task 2.5).

Two stages, deliberately separated so the LLM never picks the catalog id:

1. `RawOrder` — what the LLM emits: the customer's RAW product words + quantity.
   The model does NOT choose a product id. This stops it from "helpfully"
   substituting a real id for an unknown request (mapping بيتزا → كعك's id).
2. `ParsedOrder` — what our CODE produces after matching each raw phrase to the
   catalog deterministically. A phrase with no good catalog match is rejected
   here, in code (constitution: enforced in code, never in the prompt).

The service still re-validates every resolved line against the live catalog.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

FulfillmentType = Literal["pickup", "delivery"]


class RawOrderItem(BaseModel):
    """One requested line as the LLM heard it — the customer's own words, no id.
    Code (not the model) maps `product_phrase` to a catalog product."""

    product_phrase: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class RawOrder(BaseModel):
    """The LLM's structured reading of the message — raw phrases, no catalog ids."""

    items: list[RawOrderItem] = Field(default_factory=list)
    fulfillment_type: FulfillmentType = "pickup"
    # The customer's raw Arabic time phrase ("بكرا الصبح"), kept verbatim.
    requested_time_text: str | None = None
    note: str | None = None


class ParsedOrderItem(BaseModel):
    """One resolved line: a real catalog product id our code matched, + quantity."""

    product_id: UUID
    quantity: int = Field(ge=1)


class ParsedOrder(BaseModel):
    """The resolved order after code-side catalog matching. Only lines whose raw
    phrase matched a real catalog product survive."""

    items: list[ParsedOrderItem] = Field(min_length=1)
    fulfillment_type: FulfillmentType = "pickup"
    requested_time_text: str | None = None
    note: str | None = None


# `ConfirmedOrder` is the validated payload the service accepts. Same shape as
# `ParsedOrder` in Phase 2 (kept distinct so confirm-time fields — e.g. an
# approval token in later phases — can diverge without touching parsing).
class ConfirmedOrder(ParsedOrder):
    pass
