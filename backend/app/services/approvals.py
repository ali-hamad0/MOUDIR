"""The owner's HIL approvals service (Phase 4, Task 4.12).

This is the application-layer surface the owner's inbox routes call. It reads the
inbox (pending drafts + the dispatch_failed manual queue) and drives the three
human decisions — approve, reject, mark-sent — each tenant-scoped and audited.

It owns the COMMIT (mirrors InventoryService: the transaction commits once here),
delegating every PO state transition to `PurchaseOrderService`, which is the ONLY
PO-state writer (it flushes, joining this transaction). The split matters for the
gate:

  - `approve` here only moves the row to `approved` and commits. It does NOT mint
    the dispatch token and does NOT send — that happens in the route AFTER this
    commit returns (mint the signed token, fire dispatch as a background task).
    Keeping the send out of the transaction is deliberate (constitution V + the
    ROADMAP pitfall): a down supplier must never hang or roll back the approve.

The `status == "approved"` this writes is the lifecycle MARKER, not the gate. The
gate is the signed token the route mints and the dispatcher verifies.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.approvals import ApprovalRead
from app.db.models import Product, PurchaseOrder, Supplier
from app.repositories.products import ProductRepository
from app.repositories.purchase_orders import INBOX_STATUSES, PurchaseOrderRepository
from app.repositories.suppliers import SupplierRepository
from app.services.purchase_orders import PurchaseOrderService


def _to_read(po: PurchaseOrder, product: Product | None, supplier: Supplier | None) -> ApprovalRead:
    """Map a PO (joined to its product, and supplier if set) to the read model.

    product may be None only in the defensive case the catalog row vanished; the
    name fields then fall back to empty so the response still validates rather
    than 500s on a half-deleted product.
    """
    return ApprovalRead(
        id=po.id,
        status=po.status,
        quantity=po.quantity,
        product_id=po.product_id,
        product_name_ar=product.name_ar if product is not None else "",
        product_name_en=product.name_en if product is not None else None,
        supplier_id=po.supplier_id,
        supplier_name=supplier.name if supplier is not None else None,
        draft_reason=po.draft_reason,
        agent_note_ar=po.agent_note_ar,
        reject_reason=po.reject_reason,
        dispatch_error=po.dispatch_error,
        dispatch_attempts=po.dispatch_attempts,
        reviewed_at=po.reviewed_at,
        dispatched_at=po.dispatched_at,
        created_at=po.created_at,
    )


class ApprovalsService:
    """Read the inbox and apply the owner's HIL decisions, all tenant-scoped.

    Commits once (the unit of work for a single owner action). PO transitions go
    through PurchaseOrderService so the lifecycle stays guarded in one place.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PurchaseOrderRepository(session)
        self._products = ProductRepository(session)
        self._suppliers = SupplierRepository(session)
        self._po = PurchaseOrderService(session)

    async def list_inbox(
        self,
        *,
        tenant_id: UUID,
        statuses: Sequence[str] = INBOX_STATUSES,
        limit: int,
        offset: int,
    ) -> tuple[int, list[ApprovalRead]]:
        """One page of this tenant's inbox plus the total count, both scoped.

        Joins each PO to its product (and supplier, if set) for display — the
        JOINs are tenant-scoped on both sides in the repo (constitution I)."""
        total = await self._repo.count_for_inbox(tenant_id, statuses=statuses)
        rows = await self._repo.list_for_inbox(
            tenant_id, statuses=statuses, limit=limit, offset=offset
        )
        items = [_to_read(po, product, supplier) for po, product, supplier in rows]
        return total, items

    async def _read_for(self, tenant_id: UUID, po: PurchaseOrder) -> ApprovalRead:
        """Build the read model for a single PO after a transition, loading its
        product (and supplier, if set) tenant-scoped for display."""
        product = await self._products.get(tenant_id, po.product_id)
        supplier = (
            await self._suppliers.get(tenant_id, po.supplier_id)
            if po.supplier_id is not None
            else None
        )
        return _to_read(po, product, supplier)

    async def approve(
        self, *, tenant_id: UUID, po_id: UUID, approver_id: UUID
    ) -> tuple[PurchaseOrder, ApprovalRead]:
        """Approve a draft PO and COMMIT. Returns (po, read): the route uses the PO
        to mint the dispatch token and fire the background send AFTER this commit;
        the read model is the response body.

        No token is minted and nothing is sent here — `approved` is the lifecycle
        marker, the gate is the token the route mints next (constitution V)."""
        po = await self._po.approve(tenant_id=tenant_id, po_id=po_id, approver_id=approver_id)
        read = await self._read_for(tenant_id, po)
        await self._session.commit()
        return po, read

    async def reject(
        self, *, tenant_id: UUID, po_id: UUID, approver_id: UUID, reason: str
    ) -> ApprovalRead:
        """Reject a draft PO with a required reason, and COMMIT. Provisions nothing
        — a rejected PO never dispatches."""
        po = await self._po.reject(
            tenant_id=tenant_id, po_id=po_id, approver_id=approver_id, reason=reason
        )
        read = await self._read_for(tenant_id, po)
        await self._session.commit()
        return read

    async def mark_sent_manually(
        self, *, tenant_id: UUID, po_id: UUID, actor_id: UUID
    ) -> ApprovalRead:
        """Close a `dispatch_failed` PO the owner sent out of band, and COMMIT.
        Audited as `po.sent_manually` (in PurchaseOrderService)."""
        po = await self._po.mark_sent_manually(tenant_id=tenant_id, po_id=po_id, actor_id=actor_id)
        read = await self._read_for(tenant_id, po)
        await self._session.commit()
        return read
