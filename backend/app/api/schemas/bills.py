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

from pydantic import BaseModel


class BillRead(BaseModel):
    """One supplier bill for the review list, joined to its supplier (if mapped).

    Carries the lifecycle status and the review signals (date, total, currency,
    min_confidence) so the dashboard can show what needs attention. The image bytes
    live in MinIO; this read model carries only the reference fields, not the image
    (the side-by-side image URL is the bill-detail endpoint, Task 5.12).
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
