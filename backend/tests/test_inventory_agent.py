"""Task 4.8 — InventoryAgent graph + tools (LLM mocked).

Proves the agent drafts a reorder PO for a low product — with a sensible quantity
and a Lebanese-Arabic note — and NEVER sends it (status `draft`, dispatched_at
null). A malformed LLM note retries then falls back to a templated note (never a
500); forecast_demand is the deterministic placeholder; a product with no
inventory row drafts nothing. The LLM is faked — the suite stays offline.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.inventory.agent import InventoryAgent
from app.agents.inventory.schemas import SupplierNote
from app.agents.inventory.tools import (
    DEFAULT_REORDER_QTY,
    ToolContext,
    forecast_demand,
)
from app.db.models import Inventory, Product, PurchaseOrder, Supplier, Tenant
from app.infra.settings import Settings
from prompts import inventory_agent_ar


# ---- LLM fakes (same shape as the order-agent tests) ----
class _FakeStructured:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeModel:
    def __init__(self, structured):
        self._s = structured

    def with_structured_output(self, schema):
        return self._s


class _FakeRouter:
    def __init__(self, structured):
        self._m = _FakeModel(structured)

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


@dataclass
class _Seed:
    tenant_id: UUID
    product_id: UUID
    supplier_id: UUID


async def _seed(db: AsyncSession, *, quantity: int = 2, reorder_quantity: int | None = 12) -> _Seed:
    tenant = Tenant(name="ShopA", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    product = Product(tenant_id=tenant.id, name_ar="كعك", price_lbp=1000)
    supplier = Supplier(tenant_id=tenant.id, name="مورّد", webhook_url="https://x/y")
    db.add_all([product, supplier])
    await db.flush()
    db.add(
        Inventory(
            tenant_id=tenant.id,
            product_id=product.id,
            quantity=quantity,
            reorder_threshold=5,
            reorder_quantity=reorder_quantity,
            supplier_id=supplier.id,
        )
    )
    await db.flush()
    return _Seed(tenant_id=tenant.id, product_id=product.id, supplier_id=supplier.id)


def _agent(db, router) -> InventoryAgent:
    return InventoryAgent(router, _settings(), _sessionmaker_for(db))


async def _po_for(db: AsyncSession, tenant_id: UUID) -> PurchaseOrder | None:
    return (
        await db.execute(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant_id))
    ).scalar_one_or_none()


# ── forecast_demand: the deterministic placeholder ──────────────────────────


def _ctx(db, seed, router) -> ToolContext:
    return ToolContext(session=db, tenant_id=seed.tenant_id, router=router, settings=_settings())


async def test_forecast_uses_reorder_quantity_when_set(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, reorder_quantity=12)
    inv = (
        await db_session.execute(select(Inventory).where(Inventory.product_id == seed.product_id))
    ).scalar_one()
    assert forecast_demand(_ctx(db_session, seed, _FakeRouter(_FakeStructured([]))), inv) == 12


async def test_forecast_falls_back_to_default_when_unset(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, reorder_quantity=None)
    inv = (
        await db_session.execute(select(Inventory).where(Inventory.product_id == seed.product_id))
    ).scalar_one()
    assert (
        forecast_demand(_ctx(db_session, seed, _FakeRouter(_FakeStructured([]))), inv)
        == DEFAULT_REORDER_QTY
    )


# ── draft_for_low_stock: drafts a PO, never sends ───────────────────────────


async def test_draft_for_low_stock_creates_unsent_draft(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, reorder_quantity=12)
    note = SupplierNote(note_ar="بدنا نطلب كمان كعك لو سمحت")
    agent = _agent(db_session, _FakeRouter(_FakeStructured([note])))

    po_id = await agent.draft_for_low_stock(seed.tenant_id, seed.product_id)

    assert po_id is not None
    po = await _po_for(db_session, seed.tenant_id)
    assert po is not None
    assert po.status == "draft"  # NEVER sent
    assert po.dispatched_at is None
    assert po.quantity == 12  # from reorder_quantity via forecast_demand
    assert po.supplier_id == seed.supplier_id
    assert po.agent_note_ar == "بدنا نطلب كمان كعك لو سمحت"


async def test_malformed_note_retries_then_falls_back_no_crash(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    # Three bad outputs (llm_max_retries default 2 → 3 attempts), all fail → the
    # tool falls back to a templated Arabic note and STILL produces a draft.
    structured = _FakeStructured([ValueError("bad"), ValueError("bad"), ValueError("bad")])
    agent = _agent(db_session, _FakeRouter(structured))

    po_id = await agent.draft_for_low_stock(seed.tenant_id, seed.product_id)

    assert po_id is not None
    assert structured.calls == 3  # retried, did not crash
    po = await _po_for(db_session, seed.tenant_id)
    assert po is not None
    assert po.status == "draft"
    # The fallback note was used (templated, not None).
    assert po.agent_note_ar == inventory_agent_ar.FALLBACK_NOTE.format(
        product_name="كعك", quantity=po.quantity
    )


async def test_provider_error_falls_back_to_template(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)

    class _ProviderError(Exception):
        """Stand-in for a provider auth/rate-limit/timeout failure."""

    agent = _agent(db_session, _FakeRouter(_FakeStructured([_ProviderError("429")])))
    po_id = await agent.draft_for_low_stock(seed.tenant_id, seed.product_id)

    assert po_id is not None
    po = await _po_for(db_session, seed.tenant_id)
    assert po is not None and po.status == "draft"


async def test_no_inventory_row_drafts_nothing(db_session: AsyncSession) -> None:
    # A product the tenant doesn't track (no inventory row) → nothing to reorder.
    tenant = Tenant(name="ShopX", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db_session.add(tenant)
    await db_session.flush()
    product = Product(tenant_id=tenant.id, name_ar="قهوة", price_lbp=3000)
    db_session.add(product)
    await db_session.flush()

    agent = _agent(db_session, _FakeRouter(_FakeStructured([])))
    po_id = await agent.draft_for_low_stock(tenant.id, product.id)

    assert po_id is None
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tenant.id)
        )
    ).scalar_one()
    assert count == 0


async def test_draft_is_audited_and_breadcrumbed(db_session: AsyncSession) -> None:
    from app.db.models import AuditLog, PurchaseOrderEvent

    seed = await _seed(db_session)
    note = SupplierNote(note_ar="طلب بضاعة")
    agent = _agent(db_session, _FakeRouter(_FakeStructured([note])))
    po_id = await agent.draft_for_low_stock(seed.tenant_id, seed.product_id)

    audits = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "po.drafted", AuditLog.target == str(po_id))
        )
    ).scalar_one()
    assert audits == 1
    events = (
        await db_session.execute(
            select(func.count())
            .select_from(PurchaseOrderEvent)
            .where(
                PurchaseOrderEvent.purchase_order_id == po_id,
                PurchaseOrderEvent.event == "drafted",
            )
        )
    ).scalar_one()
    assert events == 1
