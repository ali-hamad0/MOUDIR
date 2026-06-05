"""The owner's HIL approvals inbox API (Phase 4, Task 4.12).

This is where the three Phase-4 pillars finally meet:
  - PurchaseOrderService (4.7) owns the lifecycle transition,
  - the ActionGate (4.10) mints the signed approval token,
  - the SupplierDispatcher (4.11) sends — but only behind that token.

Every route is tenant-scoped via `get_current_user` (tenant_id from the JWT,
never the path/body — the Wall) and every action is audited (in the service
layer). The send is deliberately decoupled from the approve request:

    approve  ──► PurchaseOrderService.approve + COMMIT
             ──► mint_approval_token(po.dispatch, po_id, tenant, approver)
             ──► BackgroundTasks: SupplierDispatcher.dispatch(po, supplier, token)
             ──► return 200 immediately (status "approved")

Dispatch runs AFTER the commit and OUT OF BAND (a background task), so a slow or
down supplier can never hang the approve call or roll it back (ROADMAP pitfall).
The PO row is the source of truth; if the background send fails its retries the
PO becomes `dispatch_failed` and reappears here in the manual queue.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.approvals import (
    ApprovalRead,
    ApprovalsPage,
    ApproveRequest,
    RejectRequest,
)
from app.db.models import User
from app.db.session import get_db_session
from app.infra.action_gate import mint_approval_token
from app.infra.logging import get_logger
from app.infra.settings import Settings, get_settings
from app.infra.supplier_dispatch import DISPATCH_ACTION
from app.repositories.suppliers import SupplierRepository
from app.services.approvals import ApprovalsService
from app.services.purchase_orders import (
    InvalidPurchaseOrderTransition,
    PurchaseOrderNotFound,
)
from prompts import inventory_ar

router = APIRouter(tags=["approvals"])
log = get_logger(__name__)

# Tenant scope comes from the authenticated user's JWT, never the request body.
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]
Config = Annotated[Settings, Depends(get_settings)]


@router.get("/approvals", response_model=ApprovalsPage)
async def list_approvals(
    user: CurrentUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApprovalsPage:
    """This tenant's HIL inbox: pending `draft` POs + the `dispatch_failed` manual
    queue, joined to product/supplier, newest first. Scoped to the JWT tenant —
    never shows another tenant's POs (the Wall)."""
    total, items = await ApprovalsService(db).list_inbox(
        tenant_id=user.tenant_id, limit=limit, offset=offset
    )
    return ApprovalsPage(items=items, total=total, limit=limit, offset=offset)


@router.post("/approvals/{po_id}/approve", response_model=ApprovalRead)
async def approve_purchase_order(
    po_id: UUID,
    payload: ApproveRequest,
    request: Request,
    user: CurrentUser,
    db: Db,
    settings: Config,
    background: BackgroundTasks,
) -> ApprovalRead:
    """Approve a draft PO, then mint a signed dispatch token and fire the supplier
    send as a BACKGROUND task — the call returns immediately with status
    `approved`, even if the supplier is slow or down.

    404 if the PO isn't this tenant's; 409 on an invalid transition (e.g. it's
    already approved). The optional note is recorded on the audit trail via the
    service. The token authorizes EXACTLY this (po.dispatch, po_id, tenant,
    approver) — the dispatcher re-verifies it before any side effect."""
    tenant_id = user.tenant_id
    try:
        po, read = await ApprovalsService(db).approve(
            tenant_id=tenant_id, po_id=po_id, approver_id=user.id
        )
    except PurchaseOrderNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, inventory_ar.PO_NOT_FOUND) from None
    except InvalidPurchaseOrderTransition:
        raise HTTPException(status.HTTP_409_CONFLICT, inventory_ar.PO_INVALID_TRANSITION) from None

    # The optional approver note documents the API contract the inbox UI (4.15)
    # sends. It is not persisted yet — when a home for it is added (a column or an
    # audit detail), wire it through PurchaseOrderService.approve. Logged for now
    # so the decision is auditable and the field is not silently dropped.
    if payload.note:
        log.info("approvals.approve.note", po_id=str(po.id), tenant_id=str(tenant_id))

    # The approve transaction has COMMITTED. Mint the signed token now (the gate's
    # "yes", bound to this exact action/resource/tenant/approver) and schedule the
    # dispatch out of band — never inline, so a slow supplier can't hang the call.
    token = mint_approval_token(
        settings,
        action=DISPATCH_ACTION,
        resource_id=po.id,
        tenant_id=tenant_id,
        approver_id=user.id,
    )
    supplier = None
    if po.supplier_id is not None:
        supplier = await SupplierRepository(db).get(tenant_id, po.supplier_id)
    dispatcher = request.app.state.supplier_dispatcher
    background.add_task(dispatcher.dispatch, po, supplier, token)

    return read


@router.post("/approvals/{po_id}/reject", response_model=ApprovalRead)
async def reject_purchase_order(
    po_id: UUID, payload: RejectRequest, user: CurrentUser, db: Db
) -> ApprovalRead:
    """Reject a draft PO. reason is required (422 if absent — schema-enforced).
    Provisions nothing; the PO is recorded `rejected` with the reason and audited.
    404 if it isn't this tenant's; 409 on an invalid transition."""
    tenant_id = user.tenant_id
    try:
        return await ApprovalsService(db).reject(
            tenant_id=tenant_id, po_id=po_id, approver_id=user.id, reason=payload.reason
        )
    except PurchaseOrderNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, inventory_ar.PO_NOT_FOUND) from None
    except InvalidPurchaseOrderTransition:
        raise HTTPException(status.HTTP_409_CONFLICT, inventory_ar.PO_INVALID_TRANSITION) from None


@router.post("/approvals/{po_id}/mark-sent", response_model=ApprovalRead)
async def mark_purchase_order_sent(po_id: UUID, user: CurrentUser, db: Db) -> ApprovalRead:
    """Manually close a `dispatch_failed` PO the owner sent out of band →
    `sent`, audited `po.sent_manually`. 404 if not this tenant's; 409 if the PO
    isn't in the manual queue."""
    tenant_id = user.tenant_id
    try:
        return await ApprovalsService(db).mark_sent_manually(
            tenant_id=tenant_id, po_id=po_id, actor_id=user.id
        )
    except PurchaseOrderNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, inventory_ar.PO_NOT_FOUND) from None
    except InvalidPurchaseOrderTransition:
        raise HTTPException(status.HTTP_409_CONFLICT, inventory_ar.PO_INVALID_TRANSITION) from None
