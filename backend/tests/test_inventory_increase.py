"""Task 5.10 — atomic inventory increase + ensure_row upsert.

Proves the stock-in mirror of deduct: increase() adds quantity atomically (a single
guarded UPDATE, not read-then-write), ensure_row() upserts a zero-quantity row
race-safely (ON CONFLICT DO NOTHING) so a received bill can be the first stock for a
SKU, and both stay inside the tenant's scope (the Wall). These back the gated bill
commit (Task 5.11).
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Inventory, Product, Tenant
from app.repositories.inventory import InventoryRepository


@dataclass
class _Seed:
    tenant_id: UUID
    tracked_id: UUID  # has an inventory row
    untracked_id: UUID  # product exists, no inventory row yet


async def _seed(db: AsyncSession, *, qty: int = 5) -> _Seed:
    tenant = Tenant(name="ShopA", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    tracked = Product(tenant_id=tenant.id, name_ar="طحين", price_lbp=1000)
    untracked = Product(tenant_id=tenant.id, name_ar="سكر", price_lbp=2000)
    db.add_all([tracked, untracked])
    await db.flush()
    db.add(Inventory(tenant_id=tenant.id, product_id=tracked.id, quantity=qty))
    await db.flush()
    return _Seed(tenant_id=tenant.id, tracked_id=tracked.id, untracked_id=untracked.id)


async def _qty(db: AsyncSession, tenant_id: UUID, product_id: UUID) -> int | None:
    row = await InventoryRepository(db).get_by_product(tenant_id, product_id)
    return row.quantity if row else None


# ── increase ─────────────────────────────────────────────────────────────────


async def test_increase_adds_quantity(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, qty=5)
    ok = await InventoryRepository(db_session).increase(seed.tenant_id, seed.tracked_id, 3)
    assert ok is True
    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 8


async def test_increase_returns_false_when_no_row(db_session: AsyncSession) -> None:
    """No inventory row → nothing updated (the committer calls ensure_row first)."""
    seed = await _seed(db_session)
    ok = await InventoryRepository(db_session).increase(seed.tenant_id, seed.untracked_id, 10)
    assert ok is False
    assert await _qty(db_session, seed.tenant_id, seed.untracked_id) is None


async def test_increase_is_tenant_scoped(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, qty=5)
    other_tenant = uuid4()
    ok = await InventoryRepository(db_session).increase(other_tenant, seed.tracked_id, 10)
    assert ok is False  # wrong tenant → no row matched
    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 5  # untouched


# ── ensure_row ───────────────────────────────────────────────────────────────


async def test_ensure_row_creates_zero_row(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    assert await _qty(db_session, seed.tenant_id, seed.untracked_id) is None

    await InventoryRepository(db_session).ensure_row(seed.tenant_id, seed.untracked_id)
    assert await _qty(db_session, seed.tenant_id, seed.untracked_id) == 0

    # Now increase lifts it from zero — the first-stock-for-a-SKU path.
    ok = await InventoryRepository(db_session).increase(seed.tenant_id, seed.untracked_id, 12)
    assert ok is True
    assert await _qty(db_session, seed.tenant_id, seed.untracked_id) == 12


async def test_ensure_row_is_idempotent_and_preserves_quantity(db_session: AsyncSession) -> None:
    """ensure_row on an EXISTING row is a no-op — it never resets the real quantity
    (ON CONFLICT DO NOTHING)."""
    seed = await _seed(db_session, qty=7)
    await InventoryRepository(db_session).ensure_row(seed.tenant_id, seed.tracked_id)
    # The existing quantity (7) is preserved, not reset to 0.
    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 7

    # Calling it twice for a new product creates exactly one row.
    repo = InventoryRepository(db_session)
    await repo.ensure_row(seed.tenant_id, seed.untracked_id)
    await repo.ensure_row(seed.tenant_id, seed.untracked_id)
    count = (
        (
            await db_session.execute(
                select(Inventory).where(
                    Inventory.tenant_id == seed.tenant_id,
                    Inventory.product_id == seed.untracked_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(count) == 1


async def test_ensure_row_is_tenant_scoped(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    # A second REAL tenant (ensure_row inserts, so the tenant FK must exist — a
    # bogus tenant_id is correctly rejected by the DB; the committer always passes a
    # bill's real tenant_id).
    other = Tenant(name="ShopB", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db_session.add(other)
    await db_session.flush()

    await InventoryRepository(db_session).ensure_row(other.id, seed.untracked_id)
    # The row was created under the OTHER tenant, not seed's — seed still has none.
    assert await _qty(db_session, seed.tenant_id, seed.untracked_id) is None
    assert await _qty(db_session, other.id, seed.untracked_id) == 0
