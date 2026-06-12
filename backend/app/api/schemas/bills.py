"""Schemas for supplier-bill upload + listing (Phase 5, Task 5.4).

The owner photographs a paper supplier bill; it streams to MinIO and the worker
OCRs it. These schemas cover the upload acknowledgement and the review list. The
full bill detail (image URL + extracted fields + per-line confidences) is Task 5.12.

tenant_id is NEVER in any of these payloads — it comes from the authenticated
user's JWT (the Wall, constitution I). The object key is built server-side from the
tenant + bill id; it is never accepted from the client.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BillRead(BaseModel):
    """One supplier bill for the review list, joined to its supplier (if mapped).

    Carries the lifecycle status and the review signals (date, total, currency,
    min_confidence) so the dashboard can show what needs attention. The image
    bytes live in MinIO; `image_url` is a time-boxed presigned THUMBNAIL link so
    the list shows the scan itself (Phase 10) — the full side-by-side review is
    still the bill-detail endpoint (Task 5.12).
    """

    id: UUID
    status: str
    supplier_id: UUID | None = None
    supplier_name: str | None = None
    original_filename: str | None = None
    bill_date: date | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    min_confidence: Decimal | None = None
    reject_reason: str | None = None
    reviewed_at: datetime | None = None
    committed_at: datetime | None = None
    created_at: datetime
    image_url: str | None = None


class BillsPage(BaseModel):
    """A paginated page of review-list bills. `total` is the full count for this
    tenant's review list (in the requested statuses) so the dashboard can
    paginate."""

    items: list[BillRead]
    total: int
    limit: int
    offset: int


class BillUploadAccepted(BaseModel):
    """The 202 acknowledgement of an upload: the image is in MinIO and the bill is
    recorded `uploaded`, awaiting the worker. OCR has NOT run yet (it happens in the
    worker — the request never blocks on it)."""

    id: UUID
    status: str


class BillLineRead(BaseModel):
    """One extracted bill line for the review screen, joined to its mapped product
    (if any). `confidence` flags the line for closer review when low (Task 5.6)."""

    id: UUID
    raw_text: str | None = None
    name_ar: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_amount: Decimal | None = None
    line_amount: Decimal | None = None
    confidence: Decimal | None = None
    product_id: UUID | None = None
    product_name_ar: str | None = None
    committed: bool = False


class BillDetail(BaseModel):
    """The full bill for the review screen: the header fields, a time-boxed presigned
    image URL (so the owner sees the photo side-by-side), and the extracted lines.

    The image URL is generated server-side from the tenant-prefixed object key — the
    client never sees or supplies the key (the Wall)."""

    id: UUID
    status: str
    supplier_id: UUID | None = None
    supplier_name: str | None = None
    original_filename: str | None = None
    bill_date: date | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    min_confidence: Decimal | None = None
    reject_reason: str | None = None
    reviewed_at: datetime | None = None
    committed_at: datetime | None = None
    created_at: datetime
    image_url: str | None = None
    lines: list[BillLineRead]


class BillLineUpdate(BaseModel):
    """The owner's correction to one line: edited fields and/or a product mapping.

    `id` identifies the existing line; the other fields overwrite what OCR read.
    `product_id` is the mapping target (REQUIRED for a line to commit — enforced at
    approve). A null product_id leaves the line unmapped."""

    id: UUID
    name_ar: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_amount: Decimal | None = None
    line_amount: Decimal | None = None
    product_id: UUID | None = None


class BillLinesUpdate(BaseModel):
    """A batch of line corrections the review screen submits."""

    lines: list[BillLineUpdate]


class ApproveBillRequest(BaseModel):
    """Approve a bill (no body fields needed today). Kept as a model so the contract
    can grow (e.g. an approver note) without changing the route signature. The
    commit authorization is the signed token minted server-side, never the body."""


class RejectBillRequest(BaseModel):
    """Reject a bill. reason is REQUIRED (a rejection must be explainable — mirrors
    the PO reject UX). Enforced here AND in the service."""

    reason: str = Field(min_length=1, max_length=1000)
