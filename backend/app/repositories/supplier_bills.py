from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Row, func, select

from app.db.models import Product, Supplier, SupplierBill, SupplierBillLine
from app.repositories.base import TenantScopedRepository

# What the owner's bill-review list shows: bills awaiting a human (extracted) plus
# the ones that failed OCR/extraction and need attention (manual entry / retry).
REVIEW_STATUSES = ("extracted", "ocr_failed")

# What the worker claims to OCR: freshly uploaded bills not yet picked up. (A bill
# stuck in `ocr_processing` after a worker crash is re-claimable too — Task 5.8
# decides the reclaim policy; the repo just exposes the queryable set.)
CLAIMABLE_STATUSES = ("uploaded",)


class SupplierBillRepository(TenantScopedRepository[SupplierBill]):
    """Tenant-scoped supplier-bill reads. Base CRUD (get/list/add) is inherited;
    the methods below stay scoped through `_require_tenant_scope` and add the
    review listing, the worker's claimable queue, and a bill-with-lines load.

    Bill state is written ONLY through SupplierBillService — this repo reads and
    persists rows, it does not own transitions (mirrors PurchaseOrderRepository).
    """

    model = SupplierBill

    async def list_for_review(
        self,
        tenant_id: UUID,
        *,
        statuses: Sequence[str] = REVIEW_STATUSES,
        limit: int,
        offset: int,
    ) -> Sequence[Row[tuple[SupplierBill, Supplier | None]]]:
        """One page of the review list: bills in the given statuses, joined to their
        supplier (if mapped) for display, newest first.

        The supplier join is an OUTER join (a bill may have no supplier until the
        owner maps one) but still tenant-scoped on BOTH sides, so it can never pair
        in another tenant's supplier (constitution I: a cross-tenant JOIN is a Sev-1
        leak).
        """
        stmt = (
            select(SupplierBill, Supplier)
            .outerjoin(
                Supplier,
                (Supplier.id == SupplierBill.supplier_id)
                & (Supplier.tenant_id == SupplierBill.tenant_id),
            )
            .where(
                SupplierBill.tenant_id == tenant_id,
                SupplierBill.status.in_(tuple(statuses)),
            )
            .order_by(SupplierBill.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).all()

    async def count_for_review(
        self, tenant_id: UUID, *, statuses: Sequence[str] = REVIEW_STATUSES
    ) -> int:
        """The page total for the review listing, in the same tenant scope."""
        stmt = (
            select(func.count())
            .select_from(SupplierBill)
            .where(
                SupplierBill.tenant_id == tenant_id,
                SupplierBill.status.in_(tuple(statuses)),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_claimable(
        self,
        tenant_id: UUID,
        *,
        statuses: Sequence[str] = CLAIMABLE_STATUSES,
        limit: int,
    ) -> Sequence[SupplierBill]:
        """Bills the worker can pick up for OCR, oldest first (FIFO), tenant-scoped.

        The worker iterates tenants and drains this queue (Task 5.8). Oldest-first
        so a backlog is worked fairly rather than starving the earliest uploads.
        """
        stmt = (
            self._require_tenant_scope(tenant_id)
            .where(SupplierBill.status.in_(tuple(statuses)))
            .order_by(SupplierBill.created_at.asc())
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_lines(
        self, tenant_id: UUID, bill_id: UUID
    ) -> Sequence[Row[tuple[SupplierBillLine, Product | None]]]:
        """The bill's extracted lines, each joined to its mapped product (if any),
        oldest first (extraction order).

        The product join is OUTER (an unmapped line has no product) and tenant-
        scoped on BOTH sides — a line can never join to another tenant's product.
        Scoped to the tenant so another tenant's bill yields nothing.
        """
        stmt = (
            select(SupplierBillLine, Product)
            .outerjoin(
                Product,
                (Product.id == SupplierBillLine.product_id)
                & (Product.tenant_id == SupplierBillLine.tenant_id),
            )
            .where(
                SupplierBillLine.tenant_id == tenant_id,
                SupplierBillLine.supplier_bill_id == bill_id,
            )
            .order_by(SupplierBillLine.created_at.asc())
        )
        return (await self._session.execute(stmt)).all()
