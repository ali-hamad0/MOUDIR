"""Pydantic contracts for the BillExtractionAgent.

The agent's LLM step STRUCTURES the OCR text into this shape (constitution IV: the
LLM works on the text, it never reads pixels). Keeping it a strict model means bad
LLM output is a ValidationError we retry on, not a crash — the same discipline as the
order flow's RawOrder / the InventoryAgent's SupplierNote.

Everything here is what the model READS OUT of the bill; the per-field/-line
`certainty` is the model's own confidence in that read, which the worker combines
with the OCR engine's confidence into the stored per-line score (Task 5.6).
"""

from decimal import Decimal

from pydantic import BaseModel, Field


class BillLineData(BaseModel):
    """One line item the model read off the bill.

    Amounts/quantity are Decimal (money/measures — never float). All are optional
    because a real bill line may be partially legible; the worker stores what was
    read and flags low-certainty fields for the human. `certainty` is the model's
    confidence (0..1) in THIS line, combined with OCR confidence downstream.
    """

    raw_text: str | None = None
    name_ar: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_amount: Decimal | None = None
    line_amount: Decimal | None = None
    certainty: float = Field(default=0.5, ge=0.0, le=1.0)


class BillData(BaseModel):
    """The structured bill the model extracts from the OCR text.

    `bill_date` is a free string as printed (e.g. "2026-06-01" or "١/٦/٢٠٢٦") — the
    worker parses it leniently; the model is not asked to normalize a date format.
    `currency` is "LBP" | "USD" as printed. Totals/lines are Decimal. `certainty` is
    the model's overall confidence in the extraction.
    """

    supplier_name: str | None = None
    bill_date: str | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    lines: list[BillLineData] = Field(default_factory=list)
    certainty: float = Field(default=0.5, ge=0.0, le=1.0)
