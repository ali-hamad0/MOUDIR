"""Phase 10 — owner inventory edits over WhatsApp, with confirmation.

Covers the three layers of the flow:
- parse_confirmation: the deterministic «نعم/لا» reader (no LLM may decide a write).
- InventoryAgent.propose_adjustment / apply_adjustment: parse → match → confirm
  text, then the atomic, audited write (LLM mocked; real Postgres).
- OwnerSupervisor multi-turn: propose on turn 1, the pending edit persists in the
  checkpointer, and «نعم» on turn 2 applies (or «لا» cancels) — end to end through
  the supervisor graph.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.inventory.agent import InventoryAgent
from app.agents.inventory.schemas import RawAdjustment
from app.agents.supervisor.agent import OwnerSupervisor
from app.agents.supervisor.routing import parse_confirmation
from app.db.models import AuditLog, Inventory, Product, Tenant
from app.infra.settings import Settings
from prompts import inventory_ar


# ---- LLM fakes (same shape as the order agent tests) ----
class _FakeStructured:
    def __init__(self, script: list) -> None:
        self._script = list(script)

    async def ainvoke(self, messages):
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeModel:
    def __init__(self, structured):
        self._s = structured

    def with_structured_output(self, schema):
        return self._s

    async def ainvoke(self, messages):
        outcome = await self._s.ainvoke(messages)
        return SimpleNamespace(content=outcome.model_dump_json())


class _FakeRouter:
    def __init__(self, script: list) -> None:
        self._m = _FakeModel(_FakeStructured(script))

    def tier1(self):
        return self._m

    def tier1_json(self):
        return self._m

    def tier2(self):
        return self._m

    def mark_healthy(self):
        pass

    def mark_unhealthy(self):
        pass


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


async def _seed(db: AsyncSession):
    tenant = Tenant(name="مخبز", whatsapp_number=f"+961ADJ{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    product = Product(tenant_id=tenant.id, name_ar="كنافة", price_lbp=90000, is_available=True)
    db.add(product)
    await db.flush()
    db.add(Inventory(tenant_id=tenant.id, product_id=product.id, quantity=2))
    await db.flush()
    return tenant, product


async def _quantity(db: AsyncSession, tenant_id, product_id) -> int:
    stmt = select(Inventory.quantity).where(
        Inventory.tenant_id == tenant_id, Inventory.product_id == product_id
    )
    return (await db.execute(stmt)).scalar_one()


# ---- parse_confirmation ----
@pytest.mark.parametrize(
    "message", ["نعم", "أي", "اي طبعا", "تمام", "أكيد!", "نعم، وشو ناقص كمان؟", "ok"]
)
def test_parse_confirmation_yes_variants(message):
    assert parse_confirmation(message) == "yes"


@pytest.mark.parametrize("message", ["لا", "لأ", "كلا", "إلغاء", "no"])
def test_parse_confirmation_no_variants(message):
    assert parse_confirmation(message) == "no"


@pytest.mark.parametrize("message", ["", "شو ناقص؟", "زيد الكنافة ١٠", "بكرا منشوف"])
def test_parse_confirmation_neither(message):
    assert parse_confirmation(message) is None


# ---- propose_adjustment ----
async def test_propose_builds_confirmation_without_writing(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    router = _FakeRouter([RawAdjustment(action="add", product_phrase="الكنافة", quantity=20)])
    agent = InventoryAgent(router, _settings(), _sessionmaker_for(db_session))

    proposal = await agent.propose_adjustment("زيد مخزون الكنافة ٢٠", tenant.id)

    assert proposal is not None
    reply, pending = proposal
    assert reply == inventory_ar.ADJUST_CONFIRM.format(product_name="كنافة", old=2, new=22)
    assert pending == {
        "product_id": str(product.id),
        "product_name": "كنافة",
        "action": "add",
        "amount": 20,
        "old_quantity": 2,
        "new_quantity": 22,
    }
    assert await _quantity(db_session, tenant.id, product.id) == 2  # nothing written


async def test_propose_non_edit_returns_none(db_session: AsyncSession):
    tenant, _product = await _seed(db_session)
    router = _FakeRouter([RawAdjustment(action="none")])
    agent = InventoryAgent(router, _settings(), _sessionmaker_for(db_session))
    assert await agent.propose_adjustment("شو ناقص من المخزون؟", tenant.id) is None


async def test_propose_unmatched_product_replies_not_found(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    router = _FakeRouter([RawAdjustment(action="add", product_phrase="بيتزا", quantity=5)])
    agent = InventoryAgent(router, _settings(), _sessionmaker_for(db_session))

    proposal = await agent.propose_adjustment("زيد البيتزا ٥", tenant.id)

    assert proposal is not None
    reply, pending = proposal
    assert reply == inventory_ar.ADJUST_PRODUCT_NOT_FOUND.format(phrase="بيتزا")
    assert pending is None
    assert await _quantity(db_session, tenant.id, product.id) == 2


async def test_propose_subtract_beyond_stock_replies_insufficient(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    router = _FakeRouter([RawAdjustment(action="subtract", product_phrase="كنافة", quantity=5)])
    agent = InventoryAgent(router, _settings(), _sessionmaker_for(db_session))

    proposal = await agent.propose_adjustment("نزّل الكنافة ٥", tenant.id)

    assert proposal is not None
    reply, pending = proposal
    assert reply == inventory_ar.ADJUST_INSUFFICIENT.format(amount=5, product_name="كنافة", old=2)
    assert pending is None
    assert await _quantity(db_session, tenant.id, product.id) == 2


# ---- apply_adjustment ----
async def test_apply_add_writes_and_audits(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    agent = InventoryAgent(_FakeRouter([]), _settings(), _sessionmaker_for(db_session))
    pending = {
        "product_id": str(product.id),
        "product_name": "كنافة",
        "action": "add",
        "amount": 20,
        "old_quantity": 2,
        "new_quantity": 22,
    }

    reply = await agent.apply_adjustment(tenant.id, pending)

    assert reply == inventory_ar.ADJUST_APPLIED.format(product_name="كنافة", new=22)
    assert await _quantity(db_session, tenant.id, product.id) == 22
    audits = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.tenant_id == tenant.id, AuditLog.action == "inventory.adjusted")
        )
    ).scalar()
    assert audits == 1


async def test_apply_set_is_absolute(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    agent = InventoryAgent(_FakeRouter([]), _settings(), _sessionmaker_for(db_session))
    pending = {
        "product_id": str(product.id),
        "product_name": "كنافة",
        "action": "set",
        "amount": 50,
        "old_quantity": 2,
        "new_quantity": 50,
    }
    reply = await agent.apply_adjustment(tenant.id, pending)
    assert reply == inventory_ar.ADJUST_APPLIED.format(product_name="كنافة", new=50)
    assert await _quantity(db_session, tenant.id, product.id) == 50


# ---- OwnerSupervisor multi-turn ----
def _supervisor(router, inventory_agent) -> OwnerSupervisor:
    class _StubAgent:
        async def handle(self, message, tenant_id):
            return "STUB"

    return OwnerSupervisor(
        router=router,
        order_agent=_StubAgent(),
        inventory_agent=inventory_agent,
        finance_agent=_StubAgent(),
        customer_agent=_StubAgent(),
        advisor_agent=_StubAgent(),
        checkpointer=MemorySaver(),
    )


async def test_supervisor_edit_then_yes_applies(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    # Turn 1: classify → inventory, then parse → the edit. Turn 2 («نعم») is
    # deterministic — no LLM call, so the script holds exactly two outcomes.
    router = _FakeRouter(
        [
            SimpleNamespace(intent="inventory"),
            RawAdjustment(action="add", product_phrase="الكنافة", quantity=20),
        ]
    )
    agent = InventoryAgent(router, _settings(), _sessionmaker_for(db_session))
    supervisor = _supervisor(router, agent)
    session_id = str(uuid4())

    reply1 = await supervisor.handle("زيد مخزون الكنافة ٢٠", tenant.id, session_id)
    assert reply1 == inventory_ar.ADJUST_CONFIRM.format(product_name="كنافة", old=2, new=22)
    assert await _quantity(db_session, tenant.id, product.id) == 2  # awaiting «نعم»

    reply2 = await supervisor.handle("نعم", tenant.id, session_id)
    assert reply2 == inventory_ar.ADJUST_APPLIED.format(product_name="كنافة", new=22)
    assert await _quantity(db_session, tenant.id, product.id) == 22


async def test_supervisor_edit_then_no_cancels(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    router = _FakeRouter(
        [
            SimpleNamespace(intent="inventory"),
            RawAdjustment(action="add", product_phrase="كنافة", quantity=20),
        ]
    )
    agent = InventoryAgent(router, _settings(), _sessionmaker_for(db_session))
    supervisor = _supervisor(router, agent)
    session_id = str(uuid4())

    await supervisor.handle("زيد مخزون الكنافة ٢٠", tenant.id, session_id)
    reply2 = await supervisor.handle("لا", tenant.id, session_id)

    assert reply2 == inventory_ar.ADJUST_CANCELLED
    assert await _quantity(db_session, tenant.id, product.id) == 2


async def test_supervisor_other_message_drops_pending(db_session: AsyncSession):
    tenant, product = await _seed(db_session)
    # Turn 2 is neither «نعم» nor «لا» → the pending edit is dropped and the turn
    # routes normally (classified → advisor stub). A later «نعم» (turn 3) must NOT
    # resurrect the dropped edit — it routes normally too.
    router = _FakeRouter(
        [
            SimpleNamespace(intent="inventory"),
            RawAdjustment(action="add", product_phrase="كنافة", quantity=20),
            SimpleNamespace(intent="advisor"),  # turn 2 re-classification
            SimpleNamespace(intent="advisor"),  # turn 3 «نعم» with nothing pending
        ]
    )
    agent = InventoryAgent(router, _settings(), _sessionmaker_for(db_session))
    supervisor = _supervisor(router, agent)
    session_id = str(uuid4())

    await supervisor.handle("زيد مخزون الكنافة ٢٠", tenant.id, session_id)
    reply2 = await supervisor.handle("كيف المبيعات اليوم؟", tenant.id, session_id)
    assert reply2 == "STUB"

    reply3 = await supervisor.handle("نعم", tenant.id, session_id)
    assert reply3 == "STUB"
    assert await _quantity(db_session, tenant.id, product.id) == 2  # never applied
