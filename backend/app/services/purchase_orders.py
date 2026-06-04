from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PurchaseOrder, PurchaseOrderEvent
from app.repositories.purchase_orders import PurchaseOrderRepository
from app.services.audit import AuditService


class PurchaseOrderNotFound(Exception):
    """The PO id is not this tenant's (scoped lookup missed) or doesn't exist.

    Surfaces as a 404 in the API layer. A cross-tenant id lands here exactly the
    same way a deleted id does — the Wall never reveals that another tenant's PO
    exists (constitution I).
    """

    def __init__(self, po_id: UUID) -> None:
        self.po_id = po_id
        super().__init__(f"purchase order {po_id} not found")


class InvalidPurchaseOrderTransition(Exception):
    """A transition was requested from a status that does not allow it.

    e.g. approving a PO that is not `draft`, rejecting a `sent` PO. The lifecycle
    (draft → approved → sent / dispatch_failed → manual; draft → rejected) is the
    single source of truth; an out-of-order transition is a 409, never a silent
    overwrite — the service is the ONLY PO-state writer and guards every move.
    """

    def __init__(self, po_id: UUID, current: str, action: str) -> None:
        self.po_id = po_id
        self.current = current
        self.action = action
        super().__init__(f"cannot {action} purchase order {po_id} in status {current}")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PurchaseOrderService:
    """The ONLY writer of purchase-order state (mirrors OrderService being the
    only order writer). Every transition validates the CURRENT status against the
    lifecycle and is audited; nothing else in the app flips a PO's status.

    Transactions: like AuditService, the write methods flush but do NOT commit —
    they join the caller's transaction. Token minting + the actual dispatch live
    ABOVE this service (ActionGate 4.10, Approvals API 4.12): approve() only moves
    the row to `approved` and returns it, so the gate stays the single send
    authority (constitution V).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PurchaseOrderRepository(session)
        self._audit = AuditService(session)

    async def _load(self, tenant_id: UUID, po_id: UUID) -> PurchaseOrder:
        po = await self._repo.get(tenant_id, po_id)
        if po is None:
            raise PurchaseOrderNotFound(po_id)
        return po

    def _event(self, tenant_id: UUID, po_id: UUID, event: str, detail: str | None = None) -> None:
        self._session.add(
            PurchaseOrderEvent(
                tenant_id=tenant_id, purchase_order_id=po_id, event=event, detail=detail
            )
        )

    async def draft(
        self,
        *,
        tenant_id: UUID,
        product_id: UUID,
        supplier_id: UUID | None,
        quantity: int,
        reason: str | None = None,
        agent_note_ar: str | None = None,
        actor_id: UUID | None = None,
    ) -> PurchaseOrder:
        """Create a PO in `draft`. NEVER sends — it lands in the approvals inbox.

        actor_id is the agent/system when drafted inline on low stock (Task 4.9),
        so it is optional. Writes a `drafted` breadcrumb and audits `po.drafted`.
        """
        po = PurchaseOrder(
            product_id=product_id,
            supplier_id=supplier_id,
            quantity=quantity,
            status="draft",
            draft_reason=reason,
            agent_note_ar=agent_note_ar,
        )
        po = await self._repo.add(tenant_id, po)
        self._event(tenant_id, po.id, "drafted", reason)
        await self._audit.record(
            tenant_id=tenant_id, actor_id=actor_id, action="po.drafted", target=str(po.id)
        )
        await self._session.flush()
        return po

    async def approve(self, *, tenant_id: UUID, po_id: UUID, approver_id: UUID) -> PurchaseOrder:
        """Move a `draft` PO to `approved`, stamping the reviewer.

        Returns the PO; the signed dispatch token is minted in the API layer
        (ActionGate, 4.10) and dispatch is fired AFTER commit (4.12). This service
        never sends — `approved` is the lifecycle marker, not the send authority.
        """
        po = await self._load(tenant_id, po_id)
        if po.status != "draft":
            raise InvalidPurchaseOrderTransition(po_id, po.status, "approve")
        po.status = "approved"
        po.reviewed_by = approver_id
        po.reviewed_at = _utcnow()
        self._event(tenant_id, po_id, "approved")
        await self._audit.record(
            tenant_id=tenant_id, actor_id=approver_id, action="po.approved", target=str(po_id)
        )
        await self._session.flush()
        return po

    async def reject(
        self, *, tenant_id: UUID, po_id: UUID, approver_id: UUID, reason: str
    ) -> PurchaseOrder:
        """Decline a `draft` PO. reason is required (enforced here AND at the API).
        Provisions nothing — a rejected PO never dispatches."""
        po = await self._load(tenant_id, po_id)
        if po.status != "draft":
            raise InvalidPurchaseOrderTransition(po_id, po.status, "reject")
        po.status = "rejected"
        po.reviewed_by = approver_id
        po.reviewed_at = _utcnow()
        po.reject_reason = reason
        self._event(tenant_id, po_id, "rejected", reason)
        await self._audit.record(
            tenant_id=tenant_id, actor_id=approver_id, action="po.rejected", target=str(po_id)
        )
        await self._session.flush()
        return po

    async def mark_dispatched(self, *, tenant_id: UUID, po_id: UUID) -> PurchaseOrder:
        """The dispatcher confirmed delivery: `approved` → `sent`. Records the
        dispatch time and bumps the attempt counter."""
        po = await self._load(tenant_id, po_id)
        if po.status != "approved":
            raise InvalidPurchaseOrderTransition(po_id, po.status, "mark_dispatched")
        po.status = "sent"
        po.dispatched_at = _utcnow()
        po.dispatch_attempts += 1
        po.dispatch_error = None
        self._event(tenant_id, po_id, "sent")
        await self._audit.record(
            tenant_id=tenant_id, actor_id=None, action="po.sent", target=str(po_id)
        )
        await self._session.flush()
        return po

    async def mark_dispatch_failed(
        self, *, tenant_id: UUID, po_id: UUID, error: str
    ) -> PurchaseOrder:
        """The retry budget is exhausted: `approved` → `dispatch_failed`. The PO
        lands in the manual queue (4.12) with the failure reason recorded."""
        po = await self._load(tenant_id, po_id)
        if po.status != "approved":
            raise InvalidPurchaseOrderTransition(po_id, po.status, "mark_dispatch_failed")
        po.status = "dispatch_failed"
        po.dispatch_attempts += 1
        po.dispatch_error = error
        self._event(tenant_id, po_id, "dispatch_failed", error)
        await self._audit.record(
            tenant_id=tenant_id, actor_id=None, action="po.dispatch_failed", target=str(po_id)
        )
        await self._session.flush()
        return po

    async def mark_sent_manually(
        self, *, tenant_id: UUID, po_id: UUID, actor_id: UUID
    ) -> PurchaseOrder:
        """The owner sent a `dispatch_failed` PO out of band and closes it:
        `dispatch_failed` → `sent`. Audited as `po.sent_manually`."""
        po = await self._load(tenant_id, po_id)
        if po.status != "dispatch_failed":
            raise InvalidPurchaseOrderTransition(po_id, po.status, "mark_sent_manually")
        po.status = "sent"
        po.dispatched_at = _utcnow()
        self._event(tenant_id, po_id, "sent_manually")
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="po.sent_manually",
            target=str(po_id),
        )
        await self._session.flush()
        return po
