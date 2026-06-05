"""Schemas for the owner's HIL approvals inbox (Phase 4, Task 4.12).

The inbox surfaces the purchase orders that need a human: pending `draft` POs the
InventoryAgent proposed, and `dispatch_failed` POs in the manual queue. The owner
approves (optionally with a note), rejects (reason REQUIRED), or marks a failed PO
sent out of band.

tenant_id is NEVER in any of these payloads — it comes from the authenticated
user's JWT (the Wall, constitution I). The signed approval token is minted
server-side on approve; it is never accepted from the client.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ApprovalRead(BaseModel):
    """One PO in the inbox, joined to its product (and supplier, if set) for
    display. Carries the agent's Arabic note and the dispatch outcome fields so
    the manual queue can show the failure reason."""

    id: UUID
    status: str
    quantity: int
    product_id: UUID
    product_name_ar: str
    product_name_en: str | None = None
    supplier_id: UUID | None = None
    supplier_name: str | None = None
    draft_reason: str | None = None
    agent_note_ar: str | None = None
    reject_reason: str | None = None
    dispatch_error: str | None = None
    dispatch_attempts: int
    reviewed_at: datetime | None = None
    dispatched_at: datetime | None = None
    created_at: datetime


class ApprovalsPage(BaseModel):
    """A paginated page of inbox POs. `total` is the full count for this tenant's
    inbox (in the requested statuses) so the dashboard can paginate."""

    items: list[ApprovalRead]
    total: int
    limit: int
    offset: int


class ApproveRequest(BaseModel):
    """Approve a draft PO. The note is optional — a free-text reason the owner may
    record. The actual dispatch authorization is the signed token minted
    server-side, never anything in this body."""

    note: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    """Reject a draft PO. reason is REQUIRED (a rejection must be explainable —
    mirrors the founder-approvals reject UX). Enforced here AND in the service."""

    reason: str = Field(min_length=1, max_length=1000)
