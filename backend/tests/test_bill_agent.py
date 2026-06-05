"""Task 5.7 — BillExtractionAgent graph + tools (LLM mocked).

Proves the agent structures OCR text into a BillData, best-effort maps lines to the
tenant's catalog (exact + containment; unmatched stays None), and assembles the
combined OCR×extraction confidence per line. A malformed/erroring LLM degrades to an
empty extraction (never a crash). Mapping is tenant-scoped (the Wall). The LLM is
faked — the suite stays offline.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ocr.agent import BillExtractionAgent
from app.agents.ocr.schemas import BillData, BillLineData
from app.agents.ocr.tools import ToolContext, map_lines_to_products
from app.db.models import Product, Tenant
from app.infra.settings import Settings


# ---- LLM fakes (same shape as the order/inventory agent tests) ----
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
    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


@dataclass
class _Seed:
    tenant_id: UUID
    flour_id: UUID
    sugar_id: UUID


async def _seed(db: AsyncSession) -> _Seed:
    tenant = Tenant(name="ShopA", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    flour = Product(tenant_id=tenant.id, name_ar="طحين", price_lbp=1000)
    sugar = Product(tenant_id=tenant.id, name_ar="سكر", price_lbp=2000)
    db.add_all([flour, sugar])
    await db.flush()
    return _Seed(tenant_id=tenant.id, flour_id=flour.id, sugar_id=sugar.id)


def _bill_data() -> BillData:
    """A realistic extraction: flour (exact match), sugar أبيض (containment match),
    and an item not in the catalog (stays unmapped)."""
    return BillData(
        supplier_name="مؤسسة الأمين",
        bill_date="2026-06-01",
        currency="LBP",
        total_amount=Decimal("2650000"),
        certainty=0.9,
        lines=[
            BillLineData(
                name_ar="طحين",
                quantity=Decimal("50"),
                line_amount=Decimal("1000000"),
                certainty=0.9,
            ),
            BillLineData(
                name_ar="سكر أبيض",
                quantity=Decimal("25"),
                line_amount=Decimal("750000"),
                certainty=0.8,
            ),
            BillLineData(
                name_ar="زيت", quantity=Decimal("12"), line_amount=Decimal("900000"), certainty=0.4
            ),
        ],
    )


# ── Extraction + mapping + assembly ──────────────────────────────────────────


async def test_extract_maps_and_assembles_confidence(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    router = _FakeRouter(_FakeStructured([_bill_data()]))
    agent = BillExtractionAgent(router, _settings(), _sessionmaker_for(db_session))

    result = await agent.extract_for_bill(seed.tenant_id, "نص الفاتورة", ocr_confidence=0.9)

    assert result.data.supplier_name == "مؤسسة الأمين"
    assert len(result.lines) == 3

    # طحين → exact match; سكر أبيض → containment match on سكر; زيت → unmapped.
    by_name = {line.data.name_ar: line for line in result.lines}
    assert by_name["طحين"].product_id == seed.flour_id
    assert by_name["سكر أبيض"].product_id == seed.sugar_id
    assert by_name["زيت"].product_id is None

    # Per-line confidence = ocr_confidence (0.9) × line certainty.
    assert by_name["طحين"].confidence == round(0.9 * 0.9, 3)  # 0.81
    assert by_name["زيت"].confidence == round(0.9 * 0.4, 3)  # 0.36
    # min_confidence is the lowest line score.
    assert result.min_confidence == round(0.9 * 0.4, 3)


async def test_malformed_llm_degrades_to_empty(db_session: AsyncSession) -> None:
    """Every attempt raises → the extraction degrades to an empty BillData (the
    worker maps that to ocr_failed), never a crash."""
    seed = await _seed(db_session)
    # llm_max_retries default is 2 → 3 attempts; script 3 failures.
    router = _FakeRouter(_FakeStructured([ValueError("bad"), ValueError("bad"), ValueError("bad")]))
    agent = BillExtractionAgent(router, _settings(), _sessionmaker_for(db_session))

    result = await agent.extract_for_bill(seed.tenant_id, "garbled", ocr_confidence=0.5)
    assert result.data.lines == []
    assert result.data.certainty == 0.0
    assert result.min_confidence is None


async def test_llm_provider_error_degrades_without_exhausting_retries(
    db_session: AsyncSession,
) -> None:
    """A provider/transport error (not a ValidationError) stops retrying and degrades
    on the FIRST failure — only one call is made."""
    seed = await _seed(db_session)
    structured = _FakeStructured([RuntimeError("provider down"), _bill_data()])
    agent = BillExtractionAgent(_FakeRouter(structured), _settings(), _sessionmaker_for(db_session))

    result = await agent.extract_for_bill(seed.tenant_id, "x")
    assert result.data.lines == []  # degraded
    assert structured.calls == 1  # broke out, did not retry into the good script item


# ── Mapping directly: tenant scoping (the Wall) ──────────────────────────────


async def test_mapping_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _seed(db_session)
    b = await _seed(db_session)  # a different tenant with its OWN طحين/سكر
    ctx = ToolContext(
        session=db_session,
        tenant_id=a.tenant_id,
        router=_FakeRouter(_FakeStructured([])),
        settings=_settings(),
    )
    data = BillData(lines=[BillLineData(name_ar="طحين")])
    mapped = await map_lines_to_products(ctx, data)
    # Maps to A's طحين, never B's — only A's catalog is in scope.
    assert mapped == [a.flour_id]
    assert mapped[0] != b.flour_id
