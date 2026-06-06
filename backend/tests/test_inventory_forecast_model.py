"""Task 6.7 — forecast_demand backed by the trained DemandPredictor (same signature).

Proves the Phase 6 demand model now drives the InventoryAgent's reorder quantity behind the
EXACT sync `forecast_demand(ctx, inventory) -> int` signature (AD-6.5), while the documented
fallback (owner's `reorder_quantity` → fixed default) still governs when the model has no
signal (cold-start product, or the offline stub in CI/dev). The model path is shown
end-to-end through `draft_for_low_stock`: the predictor's number becomes the PO quantity,
and the pre-fetched per-product daily-demand history actually reaches the predictor. The LLM
note is faked, so the suite stays offline.

The existing InventoryAgent suite (test_inventory_agent.py) still asserts the fallback
quantities unchanged — those tenants seed no orders, so the (default stub) predictor returns
None and forecast_demand falls back exactly as before. This file adds the model path.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.inventory.agent import InventoryAgent
from app.agents.inventory.schemas import SupplierNote
from app.agents.inventory.tools import DEFAULT_REORDER_QTY, ToolContext, forecast_demand
from app.db.models import (
    Customer,
    Inventory,
    Order,
    OrderItem,
    Product,
    PurchaseOrder,
    Supplier,
    Tenant,
)
from app.infra.settings import Settings
from app.ml.predictors import StubDemandPredictor, build_demand_predictor
from app.repositories.training_data import TrainingDataRepository

# ── doubles ──────────────────────────────────────────────────────────────────


class _RecordingPredictor:
    """A DemandPredictor double: returns a fixed value and records every call so a test
    can assert the (pre-fetched) history actually reached it."""

    def __init__(self, value: int | None) -> None:
        self.value = value
        self.calls: list[tuple] = []

    def predict_quantity(self, tenant_id, product_id, history, *, as_of=None) -> int | None:
        self.calls.append((tenant_id, product_id, list(history)))
        return self.value


class _FakeStructured:
    def __init__(self, script: list) -> None:
        self._script = list(script)

    async def ainvoke(self, messages):
        return self._script.pop(0)


class _FakeRouter:
    def __init__(self, note: SupplierNote) -> None:
        self._s = _FakeStructured([note])

    def tier1(self):
        return _FakeModel(self._s)

    def tier2(self):
        return _FakeModel(self._s)


class _FakeModel:
    def __init__(self, s):
        self._s = s

    def with_structured_output(self, schema):
        return self._s


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        redis_url="redis://localhost:6379",
        vault_addr="http://localhost:8200",
        vault_token="root",
    )


def _sessionmaker_for(session: AsyncSession):
    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


# ── unit: forecast_demand chooses model vs documented fallback ───────────────


def _inv(*, reorder_quantity: int | None) -> Inventory:
    # In-memory only — forecast_demand reads just product_id + reorder_quantity.
    return Inventory(product_id=uuid4(), reorder_quantity=reorder_quantity)


def _ctx(predictor, history) -> ToolContext:
    return ToolContext(
        session=None,
        tenant_id=uuid4(),
        router=None,
        settings=None,
        demand_predictor=predictor,
        demand_history=history,
    )


def test_forecast_uses_model_when_it_has_signal() -> None:
    predictor = _RecordingPredictor(37)
    history = [(uuid4(), datetime(2025, 1, 1).date(), 5)]
    qty = forecast_demand(_ctx(predictor, history), _inv(reorder_quantity=12))
    assert qty == 37  # the model's number, not the owner's reorder_quantity
    assert predictor.calls and predictor.calls[0][2] == history  # the history reached it


def test_forecast_falls_back_when_model_returns_none() -> None:
    # Cold start / stub → None → the SAME documented fallback (owner's reorder_quantity).
    qty = forecast_demand(_ctx(_RecordingPredictor(None), []), _inv(reorder_quantity=12))
    assert qty == 12


def test_forecast_falls_back_when_model_returns_zero() -> None:
    # A draft of 0 is meaningless; a zero forecast falls back to the documented default.
    qty = forecast_demand(_ctx(_RecordingPredictor(0), []), _inv(reorder_quantity=None))
    assert qty == DEFAULT_REORDER_QTY


def test_stub_predictor_keeps_documented_fallback() -> None:
    qty = forecast_demand(_ctx(StubDemandPredictor(), []), _inv(reorder_quantity=None))
    assert qty == DEFAULT_REORDER_QTY


# ── integration: the model quantity flows through draft_for_low_stock ────────


@dataclass
class _Seed:
    tenant_id: UUID
    product_id: UUID
    supplier_id: UUID


async def _seed(
    db: AsyncSession, *, reorder_quantity: int | None = 12, order_days: int = 0
) -> _Seed:
    tenant = Tenant(name="ShopML", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    product = Product(tenant_id=tenant.id, name_ar="كعك", price_lbp=1000)
    supplier = Supplier(tenant_id=tenant.id, name="مورّد", webhook_url="https://x/y")
    customer = Customer(tenant_id=tenant.id, phone_number=f"+96170{uuid4().hex[:6]}")
    db.add_all([product, supplier, customer])
    await db.flush()
    db.add(
        Inventory(
            tenant_id=tenant.id,
            product_id=product.id,
            quantity=2,
            reorder_threshold=5,
            reorder_quantity=reorder_quantity,
            supplier_id=supplier.id,
        )
    )
    # Daily-demand history: one order per day with a fixed line quantity, on distinct days.
    base = datetime(2025, 1, 1, 12, tzinfo=UTC)
    for i in range(order_days):
        order = Order(
            tenant_id=tenant.id,
            customer_id=customer.id,
            total_lbp=1000,
            created_at=base + timedelta(days=i),
        )
        db.add(order)
        await db.flush()
        db.add(
            OrderItem(
                tenant_id=tenant.id,
                order_id=order.id,
                product_id=product.id,
                name_ar_snapshot="كعك",
                quantity=8,
            )
        )
    await db.flush()
    return _Seed(tenant_id=tenant.id, product_id=product.id, supplier_id=supplier.id)


async def _po_for(db: AsyncSession, tenant_id: UUID) -> PurchaseOrder | None:
    return (
        await db.execute(select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant_id))
    ).scalar_one_or_none()


async def test_model_quantity_drives_the_draft(db_session: AsyncSession) -> None:
    # A trained predictor injected into the agent: its number becomes the PO quantity,
    # overriding the owner's reorder_quantity, and the pre-fetched history reaches it.
    seed = await _seed(db_session, reorder_quantity=12, order_days=5)
    predictor = _RecordingPredictor(50)
    agent = InventoryAgent(
        _FakeRouter(SupplierNote(note_ar="بدنا نطلب كمان كعك")),
        _settings(),
        _sessionmaker_for(db_session),
        predictor,
    )

    po_id = await agent.draft_for_low_stock(seed.tenant_id, seed.product_id)

    po = await _po_for(db_session, seed.tenant_id)
    assert po_id is not None and po is not None
    assert po.quantity == 50  # the model's number, NOT reorder_quantity=12
    # The pre-fetch handed the predictor this product's daily series (5 days seeded).
    assert predictor.calls and len(predictor.calls[0][2]) == 5


async def test_real_artifact_drives_the_draft_end_to_end(db_session: AsyncSession) -> None:
    # The committed demand.joblib, loaded via the factory, drives the quantity end-to-end:
    # the PO quantity equals what the predictor returns for the same pre-fetched history.
    seed = await _seed(db_session, reorder_quantity=7, order_days=40)
    predictor = build_demand_predictor(
        Settings.model_construct(ml_mode="trained", ml_demand_artifact="demand.joblib")
    )
    history = await TrainingDataRepository(db_session).daily_product_demand(
        seed.tenant_id, product_id=seed.product_id
    )
    expected = predictor.predict_quantity(seed.tenant_id, seed.product_id, history)
    assert isinstance(expected, int) and expected > 0  # model has a signal here

    agent = InventoryAgent(
        _FakeRouter(SupplierNote(note_ar="بدنا نطلب كمان كعك")),
        _settings(),
        _sessionmaker_for(db_session),
        predictor,
    )
    await agent.draft_for_low_stock(seed.tenant_id, seed.product_id)

    po = await _po_for(db_session, seed.tenant_id)
    assert po is not None
    assert po.quantity == expected  # the trained model drove the reorder quantity


async def test_cold_start_product_falls_back_with_real_artifact(db_session: AsyncSession) -> None:
    # A product with NO order history → empty series → the real predictor returns None →
    # forecast_demand uses the documented fallback (owner's reorder_quantity).
    seed = await _seed(db_session, reorder_quantity=9, order_days=0)
    predictor = build_demand_predictor(
        Settings.model_construct(ml_mode="trained", ml_demand_artifact="demand.joblib")
    )
    agent = InventoryAgent(
        _FakeRouter(SupplierNote(note_ar="بدنا نطلب كمان كعك")),
        _settings(),
        _sessionmaker_for(db_session),
        predictor,
    )

    await agent.draft_for_low_stock(seed.tenant_id, seed.product_id)

    po = await _po_for(db_session, seed.tenant_id)
    assert po is not None
    assert po.quantity == 9  # fallback to reorder_quantity, model gave no signal
