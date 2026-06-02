"""Task 2.9 — OrderAgent LangGraph graph (LLM mocked).

Exercises the full graph end-to-end: catalog → parse → confirm → reply, plus the
guard branches (not understood, unavailable, not in catalog). The LLM is faked;
the agent's own sessionmaker is pointed at the test's transactional session so
writes land in the rolled-back transaction.
"""

from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.order.agent import OrderAgent
from app.agents.order.schemas import ParsedOrder, ParsedOrderItem
from app.db.models import Customer, Order, Product, Tenant
from app.domain.identity import ResolvedIdentity
from app.infra.settings import Settings
from prompts import order_ar


# ---- LLM fakes (same shape as the tools test) ----
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
    """A stand-in for async_sessionmaker that hands the agent the test's
    transactional session (so its writes roll back with the test)."""

    @asynccontextmanager
    async def _cm():
        yield session

    def _factory():
        return _cm()

    return _factory


async def _seed(db: AsyncSession):
    ta = Tenant(name="A", whatsapp_number="+961AGENTA")
    db.add(ta)
    await db.flush()
    cust = Customer(tenant_id=ta.id, phone_number="+96170AGENT")
    avail = Product(
        tenant_id=ta.id, name_ar="كعك", price_lbp=1000, price_usd=Decimal("0.50"), is_available=True
    )
    unavail = Product(tenant_id=ta.id, name_ar="منقوشة", price_lbp=2000, is_available=False)
    db.add_all([cust, avail, unavail])
    await db.flush()
    return ta, cust, avail, unavail


def _agent(db, router) -> OrderAgent:
    return OrderAgent(router, _settings(), _sessionmaker_for(db))


def _identity(tenant, customer) -> ResolvedIdentity:
    return ResolvedIdentity(tenant=tenant, role="customer", actor=customer)


async def test_happy_path_writes_order_and_confirms(db_session: AsyncSession):
    ta, cust, avail, _u = await _seed(db_session)
    good = ParsedOrder(
        items=[ParsedOrderItem(product_id=avail.id, quantity=5)],
        fulfillment_type="pickup",
        requested_time_text="بكرا الصبح",
    )
    agent = _agent(db_session, _FakeRouter(_FakeStructured([good])))
    reply = await agent.handle("بدي ٥ كعكات بكرا الصبح", _identity(ta, cust))

    assert "5000" in reply
    assert order_ar.FULFILLMENT_PICKUP in reply
    count = (
        await db_session.execute(
            select(func.count()).select_from(Order).where(Order.tenant_id == ta.id)
        )
    ).scalar()
    assert count == 1


async def test_not_in_catalog_replies_without_order(db_session: AsyncSession):
    ta, cust, _a, _u = await _seed(db_session)
    # LLM proposes a bogus id → parse drops it → no items → "didn't understand".
    bogus = ParsedOrder(items=[ParsedOrderItem(product_id=uuid4(), quantity=1)])
    agent = _agent(db_session, _FakeRouter(_FakeStructured([bogus])))
    reply = await agent.handle("بدي بيتزا", _identity(ta, cust))

    assert reply == order_ar.DID_NOT_UNDERSTAND
    count = (
        await db_session.execute(
            select(func.count()).select_from(Order).where(Order.tenant_id == ta.id)
        )
    ).scalar()
    assert count == 0


async def test_unavailable_product_is_not_offered_and_drops_to_graceful_reply(
    db_session: AsyncSession,
):
    ta, cust, _a, unavail = await _seed(db_session)
    # The model returns the unavailable product's id, but parse_order only offers
    # AVAILABLE ids — so the line is dropped at parse and never reaches confirm.
    # (The confirm-time ProductUnavailable/ProductNotInCatalog guards are proven
    # directly in test_agent_tools.py.) Result: graceful reply, no order.
    proposal = ParsedOrder(items=[ParsedOrderItem(product_id=unavail.id, quantity=1)])
    agent = _agent(db_session, _FakeRouter(_FakeStructured([proposal])))
    reply = await agent.handle("بدي منقوشة", _identity(ta, cust))
    assert reply == order_ar.DID_NOT_UNDERSTAND
    count = (
        await db_session.execute(
            select(func.count()).select_from(Order).where(Order.tenant_id == ta.id)
        )
    ).scalar()
    assert count == 0


async def test_parse_failure_replies_gracefully(db_session: AsyncSession):
    ta, cust, _a, _u = await _seed(db_session)
    agent = _agent(
        db_session,
        _FakeRouter(_FakeStructured([ValueError("x"), ValueError("y"), ValueError("z")])),
    )
    reply = await agent.handle("؟؟؟", _identity(ta, cust))
    assert reply == order_ar.DID_NOT_UNDERSTAND
