"""Task 4.5 — order-completion inventory deduction.

Proves the physical truth Part A rests on: completing a confirmed order deducts
every tracked line atomically, the oversell guard makes the last-unit race safe
and never writes a negative level, a single short line rolls the WHOLE completion
back (no partial deduction), an untracked product is skipped (not blocked), and
the deduction stays inside the tenant's scope (the Wall). The dispatch/agent
layers are exercised elsewhere; this isolates deduction.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Customer,
    Inventory,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    Tenant,
)
from app.domain.errors import InsufficientStock
from app.repositories.inventory import InventoryRepository
from app.services.order_completion import (
    OrderCompletionService,
    OrderNotCompletable,
    OrderNotFound,
)


@dataclass
class _Seed:
    """One tenant with a customer, two tracked products, and one untracked one.

    Ids are captured as PLAIN UUIDs (not live ORM attributes): the service unwinds
    a savepoint on the rollback path, which expires ORM instances — reading a plain
    UUID we already hold avoids a lazy refresh (IO) outside the async greenlet.
    """

    tenant_id: UUID
    customer_id: UUID
    p_tracked_id: UUID
    p_other_id: UUID
    p_untracked_id: UUID


async def _seed(db: AsyncSession, *, tracked_qty: int = 20, other_qty: int = 20) -> _Seed:
    tenant = Tenant(name="ShopA", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    customer = Customer(tenant_id=tenant.id, phone_number="+96170DEDUCT")
    p_tracked = Product(tenant_id=tenant.id, name_ar="كعك", price_lbp=1000)
    p_other = Product(tenant_id=tenant.id, name_ar="منقوشة", price_lbp=2000)
    p_untracked = Product(tenant_id=tenant.id, name_ar="قهوة", price_lbp=3000)
    db.add_all([customer, p_tracked, p_other, p_untracked])
    await db.flush()
    # p_untracked deliberately has NO inventory row.
    db.add_all(
        [
            Inventory(tenant_id=tenant.id, product_id=p_tracked.id, quantity=tracked_qty),
            Inventory(tenant_id=tenant.id, product_id=p_other.id, quantity=other_qty),
        ]
    )
    await db.flush()
    return _Seed(
        tenant_id=tenant.id,
        customer_id=customer.id,
        p_tracked_id=p_tracked.id,
        p_other_id=p_other.id,
        p_untracked_id=p_untracked.id,
    )


async def _make_order(
    db: AsyncSession,
    seed: _Seed,
    lines: list[tuple[UUID, int]],
    *,
    status: str = "confirmed",
) -> UUID:
    """A confirmed order with the given (product_id, qty) lines. Returns its id."""
    order = Order(tenant_id=seed.tenant_id, customer_id=seed.customer_id, status=status)
    db.add(order)
    await db.flush()
    for product_id, qty in lines:
        db.add(
            OrderItem(
                tenant_id=seed.tenant_id,
                order_id=order.id,
                product_id=product_id,
                name_ar_snapshot="snapshot",
                quantity=qty,
                unit_price_lbp=1000,
            )
        )
    await db.flush()
    return order.id


async def _qty(db: AsyncSession, tenant_id: UUID, product_id: UUID) -> int:
    """Re-read a level straight from the DB by (tenant_id, product_id) — plain
    UUIDs, so a savepoint rollback that expires ORM instances can't trigger a lazy
    refresh (IO) outside the async greenlet. The composite key is unique."""
    return (
        await db.execute(
            select(Inventory.quantity).where(
                Inventory.tenant_id == tenant_id,
                Inventory.product_id == product_id,
            )
        )
    ).scalar_one()


# ── 1. Happy path: deducts the right quantity, order becomes completed ──────


async def test_completion_deducts_and_marks_completed(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=20)
    order_id = await _make_order(db_session, seed, [(seed.p_tracked_id, 5)])

    completed = await OrderCompletionService(db_session).complete(
        tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
    )

    assert completed.status == "completed"
    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 15

    # An OrderEvent("completed") breadcrumb and an order.completed audit row land.
    events = (
        await db_session.execute(
            select(func.count())
            .select_from(OrderEvent)
            .where(OrderEvent.order_id == order_id, OrderEvent.event == "completed")
        )
    ).scalar_one()
    assert events == 1
    audits = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "order.completed", AuditLog.target == str(order_id))
        )
    ).scalar_one()
    assert audits == 1


async def test_multi_line_deducts_each_tracked_line(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=20, other_qty=10)
    order_id = await _make_order(db_session, seed, [(seed.p_tracked_id, 3), (seed.p_other_id, 4)])

    await OrderCompletionService(db_session).complete(
        tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
    )

    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 17
    assert await _qty(db_session, seed.tenant_id, seed.p_other_id) == 6


# ── 2. Oversell guard: the last-unit race is safe, level never goes negative ─


async def test_oversell_guard_last_unit_exactly_one_winner(db_session: AsyncSession) -> None:
    """Two completions racing for the last unit: exactly one succeeds.

    The race is made safe by the DB-level guarded UPDATE (`quantity >= qty`), not
    by application read-then-write. The transactional test harness runs one
    connection, so we assert the guard's mechanism directly (the task allows this
    when two real sessions aren't available): with quantity=1, the first deduct
    matches the row (rowcount 1 → success); the second finds the predicate false
    (rowcount 0 → failure). The loser writes nothing, so the level never goes
    negative — the same outcome two concurrent transactions get, because each
    deduct is a single atomic statement evaluated under the row lock.
    """
    seed = await _seed(db_session, tracked_qty=1)
    repo = InventoryRepository(db_session)

    first = await repo.deduct(seed.tenant_id, seed.p_tracked_id, 1)
    second = await repo.deduct(seed.tenant_id, seed.p_tracked_id, 1)

    assert first is True
    assert second is False
    # Exactly one unit was spent; the level is 0, never -1.
    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 0


async def test_completion_short_line_raises_and_keeps_level(db_session: AsyncSession) -> None:
    """A completion asking for more than is in stock raises InsufficientStock and
    deducts nothing from that line."""
    seed = await _seed(db_session, tracked_qty=3)
    order_id = await _make_order(db_session, seed, [(seed.p_tracked_id, 5)])

    with pytest.raises(InsufficientStock):
        await OrderCompletionService(db_session).complete(
            tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
        )

    # Level unchanged; the guarded UPDATE matched no row.
    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 3


# ── 3. Partial-failure rollback: one short line undoes the whole completion ──


async def test_partial_failure_rolls_back_everything(db_session: AsyncSession) -> None:
    """A multi-line order where the SECOND line is short: the first line's deduct
    must be rolled back too (no partial deduction), and the order stays confirmed.
    """
    seed = await _seed(db_session, tracked_qty=20, other_qty=2)
    # Line 1 (p_tracked) is satisfiable; line 2 (p_other, need 5 of 2) is short.
    order_id = await _make_order(db_session, seed, [(seed.p_tracked_id, 5), (seed.p_other_id, 5)])

    with pytest.raises(InsufficientStock):
        await OrderCompletionService(db_session).complete(
            tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
        )

    # The first line's deduct is undone — NOTHING moved.
    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 20
    assert await _qty(db_session, seed.tenant_id, seed.p_other_id) == 2
    # Order is still confirmed, never flipped to completed.
    refreshed = (
        await db_session.execute(select(Order.status).where(Order.id == order_id))
    ).scalar_one()
    assert refreshed == "confirmed"
    # No completion audit row was written.
    audits = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "order.completed", AuditLog.target == str(order_id))
        )
    ).scalar_one()
    assert audits == 0


# ── 4. The Wall: deduction stays inside the tenant's scope ───────────────────


async def test_deduct_under_wrong_tenant_scope_affects_nothing(db_session: AsyncSession) -> None:
    """A deduct for tenant A's product issued under a foreign tenant's scope
    matches no row — the level is untouched."""
    seed = await _seed(db_session, tracked_qty=10)
    other_tenant = uuid4()  # a tenant id that does not own this product

    ok = await InventoryRepository(db_session).deduct(other_tenant, seed.p_tracked_id, 3)

    assert ok is False
    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 10


async def test_completing_b_order_never_touches_a_inventory(db_session: AsyncSession) -> None:
    """Completing tenant B's order never deducts tenant A's inventory.

    B's order references B's own product; A's identically-named inventory must be
    left alone. (The completion's item lookup is tenant-scoped on both sides.)
    """
    a = await _seed(db_session, tracked_qty=10)
    b = await _seed(db_session, tracked_qty=7)
    b_order_id = await _make_order(db_session, b, [(b.p_tracked_id, 2)])

    await OrderCompletionService(db_session).complete(
        tenant_id=b.tenant_id, order_id=b_order_id, actor_id=b.customer_id
    )

    assert await _qty(db_session, b.tenant_id, b.p_tracked_id) == 5  # B drew down
    assert await _qty(db_session, a.tenant_id, a.p_tracked_id) == 10  # A untouched


async def test_cross_tenant_order_id_is_not_found(db_session: AsyncSession) -> None:
    """Tenant A cannot complete tenant B's order — the scoped load misses → 404
    domain error, and B's inventory never moves."""
    a = await _seed(db_session)
    b = await _seed(db_session, tracked_qty=7)
    b_order_id = await _make_order(db_session, b, [(b.p_tracked_id, 2)])

    with pytest.raises(OrderNotFound):
        await OrderCompletionService(db_session).complete(
            tenant_id=a.tenant_id, order_id=b_order_id, actor_id=a.customer_id
        )
    assert await _qty(db_session, b.tenant_id, b.p_tracked_id) == 7


# ── 5. Untracked product: line skipped, completion still succeeds ────────────


async def test_untracked_product_line_is_skipped(db_session: AsyncSession) -> None:
    """A product with no inventory row is untracked — its line is skipped (not
    blocked) and the completion still succeeds, deducting only tracked lines."""
    seed = await _seed(db_session, tracked_qty=8)
    order_id = await _make_order(
        db_session, seed, [(seed.p_tracked_id, 3), (seed.p_untracked_id, 99)]
    )

    completed = await OrderCompletionService(db_session).complete(
        tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
    )

    assert completed.status == "completed"
    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 5  # tracked deducted
    # The untracked product never had a row to deduct from; none was created.
    untracked_row = await InventoryRepository(db_session).get_by_product(
        seed.tenant_id, seed.p_untracked_id
    )
    assert untracked_row is None


# ── Status guard: an already-completed order is a 409, not a re-deduction ────


async def test_already_completed_order_is_rejected(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=10)
    order_id = await _make_order(db_session, seed, [(seed.p_tracked_id, 2)], status="completed")

    with pytest.raises(OrderNotCompletable):
        await OrderCompletionService(db_session).complete(
            tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
        )
    # No double deduction.
    assert await _qty(db_session, seed.tenant_id, seed.p_tracked_id) == 10
