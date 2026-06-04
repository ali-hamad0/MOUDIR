"""Task 4.9 — inline reorder-PO drafting on order completion.

Proves the hook wired into OrderCompletionService: after a completion deducts
stock, any product now at/below its reorder threshold gets a `draft` PO from the
InventoryAgent — UNLESS one is already open (idempotent, no approval spam) — and a
re-draft is allowed once the open PO is rejected. Drafting runs after the
completion commit, so it never rolls back a real fulfillment, and the PO is queued
for approval, never sent. The LLM is faked; the suite stays offline.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.inventory.agent import InventoryAgent
from app.agents.inventory.schemas import SupplierNote
from app.db.models import (
    Customer,
    Inventory,
    Order,
    OrderItem,
    Product,
    PurchaseOrder,
    Supplier,
    Tenant,
    User,
)
from app.infra.security import hash_password
from app.infra.settings import Settings
from app.services.order_completion import OrderCompletionService
from app.services.purchase_orders import PurchaseOrderService


# ---- LLM fakes (same shape as the order/inventory agent tests) ----
class _FakeStructured:
    def __init__(self, note_ar: str = "بدنا نطلب كمان بضاعة") -> None:
        self._note = SupplierNote(note_ar=note_ar)

    async def ainvoke(self, messages):
        return self._note


class _FakeModel:
    def __init__(self, structured):
        self._s = structured

    def with_structured_output(self, schema):
        return self._s


class _FakeRouter:
    def __init__(self):
        self._m = _FakeModel(_FakeStructured())

    def tier1(self):
        return self._m

    def tier2(self):
        return self._m


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        redis_url="redis://localhost:6379",
        vault_addr="http://localhost:8200",
        vault_token="root",
    )


def _sessionmaker_for(session: AsyncSession):
    """Hands the agent the test's transactional session so its writes (and its
    commit, in savepoint-join mode) roll back with the test."""

    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


def _agent(db: AsyncSession) -> InventoryAgent:
    return InventoryAgent(_FakeRouter(), _settings(), _sessionmaker_for(db))


@dataclass
class _Seed:
    tenant_id: UUID
    customer_id: UUID
    user_id: UUID
    product_id: UUID
    supplier_id: UUID


async def _seed(
    db: AsyncSession, *, quantity: int = 6, threshold: int = 5, reorder_quantity: int = 12
) -> _Seed:
    tenant = Tenant(name="ShopA", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    customer = Customer(tenant_id=tenant.id, phone_number="+96170DRAFT")
    user = User(
        tenant_id=tenant.id,
        email=f"owner-{uuid4().hex[:6]}@a.com",
        hashed_password=hash_password("password123"),
        role="owner",
    )
    product = Product(tenant_id=tenant.id, name_ar="كعك", price_lbp=1000)
    supplier = Supplier(tenant_id=tenant.id, name="مورّد", webhook_url="https://x/y")
    db.add_all([customer, user, product, supplier])
    await db.flush()
    db.add(
        Inventory(
            tenant_id=tenant.id,
            product_id=product.id,
            quantity=quantity,
            reorder_threshold=threshold,
            reorder_quantity=reorder_quantity,
            supplier_id=supplier.id,
        )
    )
    await db.flush()
    return _Seed(
        tenant_id=tenant.id,
        customer_id=customer.id,
        user_id=user.id,
        product_id=product.id,
        supplier_id=supplier.id,
    )


async def _make_order(db: AsyncSession, seed: _Seed, qty: int) -> UUID:
    order = Order(tenant_id=seed.tenant_id, customer_id=seed.customer_id, status="confirmed")
    db.add(order)
    await db.flush()
    db.add(
        OrderItem(
            tenant_id=seed.tenant_id,
            order_id=order.id,
            product_id=seed.product_id,
            name_ar_snapshot="كعك",
            quantity=qty,
            unit_price_lbp=1000,
        )
    )
    await db.flush()
    return order.id


async def _pos_for(db: AsyncSession, tenant_id: UUID) -> list[PurchaseOrder]:
    return list(
        (
            await db.execute(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant_id))
        ).scalars()
    )


async def _complete(db: AsyncSession, seed: _Seed, order_id: UUID) -> None:
    await OrderCompletionService(db, inventory_agent=_agent(db)).complete(
        tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
    )


# ── 1. Completion that drops stock low drafts an unsent PO ──────────────────


async def test_completion_below_threshold_drafts_unsent_po(db_session: AsyncSession) -> None:
    # level 6, threshold 5; an order for 2 leaves 4 (≤ 5) → a draft PO appears.
    seed = await _seed(db_session, quantity=6, threshold=5, reorder_quantity=12)
    order_id = await _make_order(db_session, seed, 2)

    await _complete(db_session, seed, order_id)

    pos = await _pos_for(db_session, seed.tenant_id)
    assert len(pos) == 1
    po = pos[0]
    assert po.status == "draft"  # queued for approval, NEVER sent
    assert po.dispatched_at is None
    assert po.product_id == seed.product_id
    assert po.quantity == 12  # forecast_demand → reorder_quantity
    assert po.supplier_id == seed.supplier_id


# ── 2. A completion that stays above threshold drafts nothing ───────────────


async def test_completion_above_threshold_drafts_nothing(db_session: AsyncSession) -> None:
    # level 20, threshold 5; an order for 2 leaves 18 (> 5) → no draft.
    seed = await _seed(db_session, quantity=20, threshold=5)
    order_id = await _make_order(db_session, seed, 2)

    await _complete(db_session, seed, order_id)

    assert await _pos_for(db_session, seed.tenant_id) == []


# ── 3. Idempotent: a second low completion does NOT re-draft ────────────────


async def test_second_completion_does_not_redraft_open_po(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, quantity=6, threshold=5)
    first = await _make_order(db_session, seed, 2)  # 6 → 4, drafts a PO
    await _complete(db_session, seed, first)
    assert len(await _pos_for(db_session, seed.tenant_id)) == 1

    second = await _make_order(db_session, seed, 1)  # 4 → 3, still low
    await _complete(db_session, seed, second)

    # An open PO already exists → no duplicate approval is created.
    assert len(await _pos_for(db_session, seed.tenant_id)) == 1


# ── 4. After the open PO is rejected, a new draft is allowed ────────────────


async def test_redraft_allowed_after_open_po_rejected(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, quantity=6, threshold=5)
    first = await _make_order(db_session, seed, 2)
    await _complete(db_session, seed, first)
    pos = await _pos_for(db_session, seed.tenant_id)
    assert len(pos) == 1

    # Reject the draft — it is no longer "open", so the product is re-draftable.
    await PurchaseOrderService(db_session).reject(
        tenant_id=seed.tenant_id,
        po_id=pos[0].id,
        approver_id=seed.user_id,
        reason="مش هلق",
    )
    await db_session.commit()

    second = await _make_order(db_session, seed, 1)  # still low
    await _complete(db_session, seed, second)

    pos = await _pos_for(db_session, seed.tenant_id)
    statuses = sorted(p.status for p in pos)
    assert statuses == ["draft", "rejected"]  # the rejected one + a fresh draft


# ── 5. No agent injected → completion still succeeds, no draft ──────────────


async def test_no_agent_completes_without_drafting(db_session: AsyncSession) -> None:
    # Unit-style call (no agent) must still complete and simply skip drafting.
    seed = await _seed(db_session, quantity=6, threshold=5)
    order_id = await _make_order(db_session, seed, 2)

    completed = await OrderCompletionService(db_session).complete(
        tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
    )

    assert completed.status == "completed"
    assert await _pos_for(db_session, seed.tenant_id) == []


# ── 6. A drafting failure does not roll back the completion ─────────────────


async def test_draft_failure_does_not_roll_back_completion(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, quantity=6, threshold=5)
    order_id = await _make_order(db_session, seed, 2)

    class _BoomAgent:
        async def draft_for_low_stock(self, tenant_id, product_id):
            raise RuntimeError("agent down")

    completed = await OrderCompletionService(db_session, inventory_agent=_BoomAgent()).complete(
        tenant_id=seed.tenant_id, order_id=order_id, actor_id=seed.customer_id
    )

    # The order is completed and the deduction stuck even though drafting blew up.
    assert completed.status == "completed"
    level = (
        await db_session.execute(
            select(Inventory.quantity).where(Inventory.product_id == seed.product_id)
        )
    ).scalar_one()
    assert level == 4
    assert await _pos_for(db_session, seed.tenant_id) == []
