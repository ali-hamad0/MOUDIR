"""Task 2.8 — OrderAgent tools (LLM mocked).

get_products reads the tenant catalog; parse_order retries on bad LLM output then
falls back to None (no crash) and drops non-catalog ids; confirm_order re-validates
and refuses non-catalog / unavailable products. The LLM is faked — no real call.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.order.schemas import ParsedOrder, ParsedOrderItem
from app.agents.order.tools import (
    ToolContext,
    confirm_order,
    get_products,
    parse_order,
)
from app.db.models import Customer, Product, Tenant
from app.domain.errors import ProductNotInCatalog, ProductUnavailable
from app.domain.identity import ResolvedIdentity
from app.infra.settings import Settings


# ---- LLM fakes -------------------------------------------------------------
class _FakeStructured:
    """Stands in for model.with_structured_output(ParsedOrder). `script` is a list
    of outcomes: a ParsedOrder to return, or an Exception to raise (bad output)."""

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
    def __init__(self, structured: _FakeStructured) -> None:
        self._structured = structured

    def with_structured_output(self, schema):
        return self._structured


class _FakeRouter:
    def __init__(self, structured: _FakeStructured) -> None:
        self._model = _FakeModel(structured)

    def tier1(self):
        return self._model

    def tier2(self):
        return self._model


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        redis_url="redis://localhost:6379",
        vault_addr="http://localhost:8200",
        vault_token="root",
    )


async def _seed(db: AsyncSession):
    ta = Tenant(name="A", whatsapp_number="+961TOOLA")
    db.add(ta)
    await db.flush()
    cust = Customer(tenant_id=ta.id, phone_number="+96170TOOL")
    avail = Product(
        tenant_id=ta.id, name_ar="كعك", price_lbp=1000, price_usd=Decimal("0.50"), is_available=True
    )
    unavail = Product(tenant_id=ta.id, name_ar="منقوشة", price_lbp=2000, is_available=False)
    db.add_all([cust, avail, unavail])
    await db.flush()
    return ta, cust, avail, unavail


def _ctx(db, tenant, actor, router) -> ToolContext:
    identity = ResolvedIdentity(tenant=tenant, role="customer", actor=actor)
    return ToolContext(session=db, identity=identity, router=router, settings=_settings())


async def test_get_products_returns_tenant_catalog(db_session: AsyncSession):
    ta, cust, avail, unavail = await _seed(db_session)
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([])))
    catalog = await get_products(ctx)
    names = {c.name_ar for c in catalog}
    assert names == {"كعك", "منقوشة"}


async def test_parse_order_retries_then_returns_none(db_session: AsyncSession):
    ta, cust, avail, _u = await _seed(db_session)
    # Two bad outputs in a row; llm_max_retries default 2 → 3 attempts, all fail here.
    structured = _FakeStructured([ValueError("bad1"), ValueError("bad2"), ValueError("bad3")])
    ctx = _ctx(db_session, ta, cust, _FakeRouter(structured))
    catalog = await get_products(ctx)
    result = await parse_order(ctx, "بدي شي", catalog)
    assert result is None
    assert structured.calls == 3  # retried, did not crash


async def test_parse_order_drops_non_catalog_ids(db_session: AsyncSession):
    ta, cust, avail, _u = await _seed(db_session)
    # LLM returns a valid-shaped order but with a bogus product id not in catalog.
    bogus = ParsedOrder(items=[ParsedOrderItem(product_id=uuid4(), quantity=1)])
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([bogus])))
    catalog = await get_products(ctx)
    result = await parse_order(ctx, "بدي بيتزا", catalog)
    assert result is None  # the bogus line was dropped → nothing left → None


async def test_parse_order_keeps_valid_catalog_line(db_session: AsyncSession):
    ta, cust, avail, _u = await _seed(db_session)
    good = ParsedOrder(items=[ParsedOrderItem(product_id=avail.id, quantity=3)])
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([good])))
    catalog = await get_products(ctx)
    result = await parse_order(ctx, "بدي ٣ كعكات", catalog)
    assert result is not None
    assert result.items[0].product_id == avail.id
    assert result.items[0].quantity == 3


async def test_confirm_order_writes_order(db_session: AsyncSession):
    ta, cust, avail, _u = await _seed(db_session)
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([])))
    parsed = ParsedOrder(items=[ParsedOrderItem(product_id=avail.id, quantity=2)])
    order = await confirm_order(ctx, parsed)
    assert order.total_lbp == 2000


async def test_confirm_order_refuses_unavailable(db_session: AsyncSession):
    ta, cust, _a, unavail = await _seed(db_session)
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([])))
    parsed = ParsedOrder(items=[ParsedOrderItem(product_id=unavail.id, quantity=1)])
    with pytest.raises(ProductUnavailable):
        await confirm_order(ctx, parsed)


async def test_confirm_order_refuses_non_catalog(db_session: AsyncSession):
    ta, cust, _a, _u = await _seed(db_session)
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([])))
    parsed = ParsedOrder(items=[ParsedOrderItem(product_id=uuid4(), quantity=1)])
    with pytest.raises(ProductNotInCatalog):
        await confirm_order(ctx, parsed)
