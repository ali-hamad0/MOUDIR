"""Task 2.8 — OrderAgent tools (LLM mocked).

get_products reads the tenant catalog; parse_order retries on bad LLM output then
falls back to None (no crash) and drops non-catalog ids; confirm_order re-validates
and refuses non-catalog / unavailable products. The LLM is faked — no real call.
"""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.order.schemas import ParsedOrder, ParsedOrderItem, RawOrder, RawOrderItem
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

    async def ainvoke(self, messages):
        # tier1_json path: parse_order reads raw JSON text off .content, so the
        # scripted RawOrder is serialized the way the real model would emit it.
        outcome = await self._structured.ainvoke(messages)
        return SimpleNamespace(content=outcome.model_dump_json())


class _FakeRouter:
    def __init__(self, structured: _FakeStructured) -> None:
        self._model = _FakeModel(structured)

    def tier1(self):
        return self._model

    def tier1_json(self):
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


async def test_parse_order_degrades_on_provider_error(db_session: AsyncSession):
    ta, cust, _a, _u = await _seed(db_session)

    class _ProviderError(Exception):
        """Stand-in for ChatGoogleGenerativeAIError (auth / rate-limit / timeout)."""

    structured = _FakeStructured(
        [_ProviderError("429"), _ProviderError("429"), _ProviderError("429")]
    )
    ctx = _ctx(db_session, ta, cust, _FakeRouter(structured))
    catalog = await get_products(ctx)
    # A hard provider failure must degrade to None (graceful reply), never crash.
    result = await parse_order(ctx, "بدي ٥ كعكات", catalog)
    assert result is None
    assert structured.calls == 3


async def test_parse_order_drops_unmatched_phrase(db_session: AsyncSession):
    # The LLM returns the customer's RAW phrase "بيتزا" (a thing the shop doesn't
    # sell). Our code finds no catalog match → the line is dropped → None. This is
    # the substitution gap closed: the model never picks the id, so it can't map
    # بيتزا onto كعك's id.
    ta, cust, _a, _u = await _seed(db_session)
    raw = RawOrder(items=[RawOrderItem(product_phrase="بيتزا", quantity=1)])
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([raw])))
    catalog = await get_products(ctx)
    result = await parse_order(ctx, "بدي بيتزا", catalog)
    assert result is None


async def test_parse_order_matches_phrase_to_catalog(db_session: AsyncSession):
    ta, cust, avail, _u = await _seed(db_session)
    # "كعكات" (plural) must still match the catalog "كعك" via normalized overlap.
    raw = RawOrder(items=[RawOrderItem(product_phrase="كعكات", quantity=3)])
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([raw])))
    catalog = await get_products(ctx)
    result = await parse_order(ctx, "بدي ٣ كعكات", catalog)
    assert result is not None
    assert result.items[0].product_id == avail.id
    assert result.items[0].quantity == 3


def test_match_prefers_exact_over_earlier_containment():
    # Regression (found live): a shop selling both "كعك" and "كعك بالسمسم" must
    # give a sesame-kaak order the sesame product, even when plain كعك comes
    # first in the catalog — each match tier runs over the WHOLE list.
    from app.agents.order.tools import CatalogItem, _match_phrase_to_catalog

    plain = CatalogItem(id=uuid4(), name_ar="كعك", price_lbp=50_000, is_available=True)
    sesame = CatalogItem(id=uuid4(), name_ar="كعك بالسمسم", price_lbp=15_000, is_available=True)
    catalog = [plain, sesame]

    assert _match_phrase_to_catalog("كعك بالسمسم", catalog) is sesame
    # The generic phrase still resolves to the exact generic product.
    assert _match_phrase_to_catalog("كعك", catalog) is plain
    # Containment tier: an over-specified phrase picks the most specific name.
    assert _match_phrase_to_catalog("كعك بالسمسم عالطالع", catalog) is sesame


async def test_parse_order_mixed_keeps_matched_drops_unmatched(db_session: AsyncSession):
    # A mixed order: one real product (كعك) + one the shop doesn't sell (بيتزا).
    # The matched line survives; the unmatched one is dropped. No substitution.
    ta, cust, avail, _u = await _seed(db_session)
    raw = RawOrder(
        items=[
            RawOrderItem(product_phrase="بيتزا", quantity=2),
            RawOrderItem(product_phrase="كعك", quantity=1),
        ]
    )
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([raw])))
    catalog = await get_products(ctx)
    result = await parse_order(ctx, "بدي بيتزا وكعكة", catalog)
    assert result is not None
    assert len(result.items) == 1
    assert result.items[0].product_id == avail.id


async def test_parse_order_drops_unavailable_phrase(db_session: AsyncSession):
    # An unavailable product's name is not offered as a match → dropped → None.
    ta, cust, _a, _unavail = await _seed(db_session)
    raw = RawOrder(items=[RawOrderItem(product_phrase="منقوشة", quantity=1)])
    ctx = _ctx(db_session, ta, cust, _FakeRouter(_FakeStructured([raw])))
    catalog = await get_products(ctx)
    result = await parse_order(ctx, "بدي منقوشة", catalog)
    assert result is None


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
