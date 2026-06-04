from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Row, func, select, update

from app.db.models import Inventory, Product
from app.repositories.base import TenantScopedRepository


class InventoryRepository(TenantScopedRepository[Inventory]):
    """Tenant-scoped inventory reads/writes. Base CRUD (get/list/add/delete) is
    inherited; the methods below stay scoped through `_require_tenant_scope` and
    add the atomic deduct + the dashboard/reorder reads Phase 4 needs."""

    model = Inventory

    async def get_by_product(self, tenant_id: UUID, product_id: UUID) -> Inventory | None:
        """The single inventory row for a product, tenant-scoped (None if the
        product is untracked for this tenant)."""
        stmt = self._require_tenant_scope(tenant_id).where(Inventory.product_id == product_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def deduct(self, tenant_id: UUID, product_id: UUID, qty: int) -> bool:
        """Atomically remove `qty` from a product's stock. Returns True on success.

        This is the oversell guard (ROADMAP pitfall: two orders for the last
        unit). It is a SINGLE guarded UPDATE — never a read-then-write, which
        races: between the read and the write a concurrent order could spend the
        same units. The `quantity >= qty` predicate is evaluated by the database
        under the row lock, so of two concurrent deductions for the last unit
        exactly one matches a row; the loser matches nothing (rowcount 0) and
        gets False. The DB-level CHECK (quantity >= 0) backs this — no negative
        quantity is ever written. False → caller raises InsufficientStock and the
        transaction rolls back.
        """
        stmt = (
            update(Inventory)
            .where(
                Inventory.tenant_id == tenant_id,
                Inventory.product_id == product_id,
                Inventory.quantity >= qty,
            )
            .values(quantity=Inventory.quantity - qty)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def list_low_stock(self, tenant_id: UUID) -> Sequence[Inventory]:
        """Reorder candidates: rows with a threshold set and at/below it.

        A null `reorder_threshold` means the owner has not opted that product into
        reorder tracking — it never trips (and never drafts a PO), so it is
        excluded here.
        """
        stmt = self._require_tenant_scope(tenant_id).where(
            Inventory.reorder_threshold.is_not(None),
            Inventory.quantity <= Inventory.reorder_threshold,
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count(self, tenant_id: UUID) -> int:
        """How many inventory rows this tenant has — the page total for the
        dashboard view, computed in the same tenant scope as the listing."""
        stmt = select(func.count()).select_from(Inventory).where(Inventory.tenant_id == tenant_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_with_product(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> Sequence[Row[tuple[Inventory, Product]]]:
        """One page of inventory joined to the catalog for the dashboard view.

        The JOIN to products is scoped on BOTH sides by tenant_id (constitution I:
        a cross-tenant JOIN is a Sev-1 leak). Newest-catalogued first.
        """
        stmt = (
            select(Inventory, Product)
            .join(
                Product,
                (Product.id == Inventory.product_id) & (Product.tenant_id == Inventory.tenant_id),
            )
            .where(Inventory.tenant_id == tenant_id)
            .order_by(Product.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).all()
