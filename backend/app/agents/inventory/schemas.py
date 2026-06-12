"""Pydantic contracts for the InventoryAgent.

The agent's only LLM step (drafting a supplier note) returns this shape. Keeping
it a strict model means bad LLM output is a ValidationError we retry on, not a
crash — the same discipline as the order flow's RawOrder.
"""

from typing import Literal

from pydantic import BaseModel, Field


class SupplierNote(BaseModel):
    """The Tier-1 LLM's drafted supplier note, in Lebanese Arabic.

    A single non-empty string. The model is constrained to a short reorder note;
    everything factual (product, quantity) is decided in code, not by the model.
    """

    note_ar: str = Field(min_length=1)


class RawAdjustment(BaseModel):
    """The Tier-1 LLM's reading of an owner stock-edit request (Phase 10).

    Mirrors the order flow's RawOrder discipline: the model returns only the
    owner's RAW product phrase + the action/amount — it never picks a product id.
    Code matches the phrase against the tenant's catalog. `action="none"` means
    the message is not a stock edit (a question, smalltalk) and the read-only
    path handles it.
    """

    action: Literal["add", "subtract", "set", "none"] = "none"
    product_phrase: str = ""
    quantity: int | None = Field(default=None, ge=1)
