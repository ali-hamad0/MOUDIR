"""Pydantic contracts for the InventoryAgent.

The agent's only LLM step (drafting a supplier note) returns this shape. Keeping
it a strict model means bad LLM output is a ValidationError we retry on, not a
crash — the same discipline as the order flow's RawOrder.
"""

from pydantic import BaseModel, Field


class SupplierNote(BaseModel):
    """The Tier-1 LLM's drafted supplier note, in Lebanese Arabic.

    A single non-empty string. The model is constrained to a short reorder note;
    everything factual (product, quantity) is decided in code, not by the model.
    """

    note_ar: str = Field(min_length=1)
