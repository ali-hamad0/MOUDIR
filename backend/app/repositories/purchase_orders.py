from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Row, exists, func, select

from app.db.models import Product, PurchaseOrder, Supplier
from app.repositories.base import TenantScopedRepository

# A PO is "open" while it is still on the way to a supplier: drafted and awaiting
# a human, or approved but not yet sent. These are the statuses that should block
# a second draft for the same product (idempotent drafting, Task 4.9).
OPEN_PO_STATUSES = ("draft", "approved")

# What the owner's Approvals inbox shows: pending drafts + the manual queue.
INBOX_STATUSES = ("draft", "dispatch_failed")


class PurchaseOrderRepository(TenantScopedRepository[PurchaseOrder]):
    """Tenant-scoped purchase-order reads. Base CRUD (get/list/add) is inherited;
    the methods below stay scoped through `_require_tenant_scope` and add the
    inbox listing + the open-PO check the inline drafting (Task 4.9) needs.

    PO state is written ONLY through PurchaseOrderService — this repo reads and
    persists rows, it does not own transitions.
    """

    model = PurchaseOrder

    async def has_open_po_for_product(self, tenant_id: UUID, product_id: UUID) -> bool:
        """True if this product already has a draft or approved-unsent PO.

        Drives idempotent drafting (Task 4.9): a product with an open PO is not
        re-drafted, so the owner is never spammed with duplicate approvals. Scoped
        to the tenant — another tenant's open PO is invisible and irrelevant here.
        """
        stmt = select(
            exists().where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.product_id == product_id,
                PurchaseOrder.status.in_(OPEN_PO_STATUSES),
            )
        )
        return bool((await self._session.execute(stmt)).scalar())

    async def list_for_inbox(
        self,
        tenant_id: UUID,
        *,
        statuses: Sequence[str] = INBOX_STATUSES,
        limit: int,
        offset: int,
    ) -> Sequence[Row[tuple[PurchaseOrder, Product, Supplier | None]]]:
        """One page of the Approvals inbox: POs in the given statuses, joined to
        their product and (optionally) supplier for display, newest first.

        The JOINs are scoped on BOTH sides by tenant_id (constitution I: a
        cross-tenant JOIN is a Sev-1 leak). The supplier join is an OUTER join —
        a draft may exist before the owner has set a supplier — but still tenant-
        scoped, so it can never pair in another tenant's supplier.
        """
        stmt = (
            select(PurchaseOrder, Product, Supplier)
            .join(
                Product,
                (Product.id == PurchaseOrder.product_id)
                & (Product.tenant_id == PurchaseOrder.tenant_id),
            )
            .outerjoin(
                Supplier,
                (Supplier.id == PurchaseOrder.supplier_id)
                & (Supplier.tenant_id == PurchaseOrder.tenant_id),
            )
            .where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.status.in_(tuple(statuses)),
            )
            .order_by(PurchaseOrder.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).all()

    async def count_for_inbox(
        self, tenant_id: UUID, *, statuses: Sequence[str] = INBOX_STATUSES
    ) -> int:
        """The page total for the inbox listing, in the same tenant scope."""
        stmt = (
            select(func.count())
            .select_from(PurchaseOrder)
            .where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.status.in_(tuple(statuses)),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())
