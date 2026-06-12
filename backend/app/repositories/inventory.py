from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

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

    async def increase(self, tenant_id: UUID, product_id: UUID, qty: int) -> bool:
        """Atomically ADD `qty` to a product's stock. Returns True on success.

        The mirror of `deduct` for received stock (Phase 5: an approved supplier
        bill increases inventory, Task 5.11). A single guarded UPDATE — never a
        read-then-write. There is no oversell concern on an increase (the CHECK
        quantity >= 0 can't be violated by adding), but it stays one atomic
        statement so two concurrent increases both apply without losing an update.
        Returns False only if there is no inventory row for this product/tenant —
        the caller calls `ensure_row` first (a received bill may be the first stock
        for a SKU).
        """
        stmt = (
            update(Inventory)
            .where(
                Inventory.tenant_id == tenant_id,
                Inventory.product_id == product_id,
            )
            .values(quantity=Inventory.quantity + qty)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def set_quantity(self, tenant_id: UUID, product_id: UUID, qty: int) -> bool:
        """Atomically SET a product's stock to an absolute quantity. Returns True
        on success, False when no inventory row exists (caller ensures the row
        first, like `increase`). One atomic statement, same discipline as
        deduct/increase — used by the WhatsApp owner edit ("صار عندي ٥٠")."""
        stmt = (
            update(Inventory)
            .where(
                Inventory.tenant_id == tenant_id,
                Inventory.product_id == product_id,
            )
            .values(quantity=qty)
        )
        result = await self._session.execute(stmt)
        return result.rowcount == 1

    async def ensure_row(self, tenant_id: UUID, product_id: UUID) -> None:
        """Make sure an inventory row exists for this (tenant, product) at qty 0.

        A received supplier bill can be the FIRST time a SKU enters stock (the owner
        may not have tracked it yet), so the committer (Task 5.11) calls this before
        `increase`. Uses INSERT ... ON CONFLICT DO NOTHING on the (tenant_id,
        product_id) unique constraint so it is race-safe: two concurrent commits
        can't both insert a duplicate, and an existing row (with its real quantity /
        thresholds) is left untouched. Tenant-scoped — the row is forced into the
        caller's tenant, never another's.
        """
        stmt = (
            pg_insert(Inventory)
            .values(tenant_id=tenant_id, product_id=product_id, quantity=0)
            .on_conflict_do_nothing(constraint="uq_inventory_tenant_product")
        )
        await self._session.execute(stmt)

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

    async def count_products(self, tenant_id: UUID) -> int:
        """Count of this tenant's catalog — the correct page total for the
        inventory dashboard which LEFT JOINs to show ALL products, not just
        those that already have a tracked inventory row."""
        stmt = select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_with_product(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> Sequence[Row[tuple[Product, Inventory | None]]]:
        """One page of the catalog LEFT JOINed to inventory for the dashboard.

        Products with no inventory row yet appear with null inventory fields
        (quantity defaults to 0 in the read model). Tenant-scoped on both
        sides (constitution I: a cross-tenant JOIN is a Sev-1 leak).
        Newest-catalogued first.
        """
        stmt = (
            select(Product, Inventory)
            .outerjoin(
                Inventory,
                (Inventory.product_id == Product.id) & (Inventory.tenant_id == Product.tenant_id),
            )
            .where(Product.tenant_id == tenant_id)
            .order_by(Product.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).all()
