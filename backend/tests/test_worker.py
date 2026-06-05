"""Task 5.8 — the background OCR worker (BillProcessor).

Proves the processing core (separated from the poll loop/signals) drives a bill end
to end: claim → fetch from storage → preprocess → OCR (stub) → extract → persist as
`extracted` with lines and confidence. A pipeline failure lands the bill in
`ocr_failed` (never crashes the worker). Processing is tenant-scoped (the Wall):
the cross-tenant discovery only returns ids, and each bill is processed in its scope.
The OCR engine is the stub and the LLM is faked — the suite stays offline.
"""

import io
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ocr.agent import BillExtractionAgent
from app.agents.ocr.schemas import BillData, BillLineData
from app.db.models import AuditLog, Product, SupplierBill, SupplierBillLine, Tenant
from app.infra.ocr import StubOCREngine
from app.infra.settings import Settings
from app.services.supplier_bills import SupplierBillService
from app.worker import BillProcessor


# ---- fakes ----
class _FakeStructured:
    def __init__(self, data: BillData) -> None:
        self._data = data

    async def ainvoke(self, messages):
        return self._data


class _FakeModel:
    def __init__(self, data):
        self._s = _FakeStructured(data)

    def with_structured_output(self, schema):
        return self._s


class _FakeRouter:
    def __init__(self, data):
        self._m = _FakeModel(data)

    def tier1(self):
        return self._m

    def tier2(self):
        return self._m


class _FakeStorage:
    """Returns a real small PNG for any key (so preprocess has a decodable image)."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def get_bytes(self, key: str) -> bytes:
        if self._fail:
            raise RuntimeError("minio down")
        buf = io.BytesIO()
        Image.new("L", (200, 120), color=255).save(buf, format="PNG")
        return buf.getvalue()


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        redis_url="redis://localhost:6379",
        vault_addr="http://localhost:8200",
        vault_token="root",
        worker_batch_size=10,
    )


def _sessionmaker_for(session: AsyncSession):
    """Hand the worker the test's transactional session for every 'open' — the cm
    does NOT close it, so the worker can reopen it across its claim/save blocks; its
    commits create/release savepoints that roll back with the test."""

    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


def _bill_data() -> BillData:
    return BillData(
        supplier_name="مورّد",
        currency="LBP",
        total_amount=Decimal("1750000"),
        certainty=0.9,
        lines=[
            BillLineData(name_ar="طحين", quantity=Decimal("50"), certainty=0.9),
            BillLineData(name_ar="سكر", quantity=Decimal("25"), certainty=0.7),
        ],
    )


def _processor(session: AsyncSession, *, data: BillData | None = None, storage_fail: bool = False):
    settings = _settings()
    sm = _sessionmaker_for(session)
    agent = BillExtractionAgent(_FakeRouter(data or _bill_data()), settings, sm)
    return BillProcessor(
        sessionmaker=sm,
        storage=_FakeStorage(fail=storage_fail),
        ocr_engine=StubOCREngine(),
        bill_agent=agent,
        settings=settings,
    )


@dataclass
class _Seed:
    tenant_id: UUID
    flour_id: UUID
    sugar_id: UUID


async def _seed(db: AsyncSession, *, name: str = "ShopA") -> _Seed:
    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    flour = Product(tenant_id=tenant.id, name_ar="طحين", price_lbp=1000)
    sugar = Product(tenant_id=tenant.id, name_ar="سكر", price_lbp=2000)
    db.add_all([flour, sugar])
    await db.flush()
    return _Seed(tenant_id=tenant.id, flour_id=flour.id, sugar_id=sugar.id)


async def _upload(db: AsyncSession, seed: _Seed) -> UUID:
    bill = await SupplierBillService(db).create_uploaded(
        tenant_id=seed.tenant_id,
        object_key=f"bills/{seed.tenant_id}/{uuid4()}/x.png",
        original_filename="x.png",
        content_type="image/png",
    )
    return bill.id


async def _status(db: AsyncSession, bill_id: UUID) -> str:
    return (
        await db.execute(select(SupplierBill.status).where(SupplierBill.id == bill_id))
    ).scalar_one()


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_worker_processes_uploaded_bill_to_extracted(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _upload(db_session, seed)

    processed = await _processor(db_session).run_once()
    assert processed == 1

    assert await _status(db_session, bill_id) == "extracted"
    # Lines were persisted and mapped to the catalog.
    rows = (
        (
            await db_session.execute(
                select(SupplierBillLine).where(SupplierBillLine.supplier_bill_id == bill_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    by_name = {r.name_ar: r for r in rows}
    assert by_name["طحين"].product_id == seed.flour_id
    assert by_name["سكر"].product_id == seed.sugar_id
    # Confidence was stored (stub OCR conf 0.95 × line certainty).
    assert by_name["طحين"].confidence is not None


async def test_worker_records_engine_and_min_confidence(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _upload(db_session, seed)
    await _processor(db_session).run_once()

    bill = (
        await db_session.execute(select(SupplierBill).where(SupplierBill.id == bill_id))
    ).scalar_one()
    assert bill.ocr_engine == "stub"
    assert bill.min_confidence is not None
    assert bill.currency == "LBP"
    assert bill.extracted is not None  # the structured BillData JSON


# ── Failure path ─────────────────────────────────────────────────────────────


async def test_pipeline_failure_marks_ocr_failed(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _upload(db_session, seed)

    # Storage fails → the bill lands in ocr_failed, the worker does not crash.
    await _processor(db_session, storage_fail=True).run_once()

    assert await _status(db_session, bill_id) == "ocr_failed"
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "bill.ocr_failed", AuditLog.target == str(bill_id))
        )
    ).scalar_one()
    assert n == 1


# ── The Wall: tenant scoping ─────────────────────────────────────────────────


async def test_worker_processes_each_tenant_in_scope(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    a_bill = await _upload(db_session, a)
    b_bill = await _upload(db_session, b)

    processed = await _processor(db_session).run_once()
    assert processed == 2  # both tenants' bills

    # Each bill became extracted under its OWN tenant; lines stay in scope.
    assert await _status(db_session, a_bill) == "extracted"
    assert await _status(db_session, b_bill) == "extracted"
    a_lines = (
        (
            await db_session.execute(
                select(SupplierBillLine).where(SupplierBillLine.supplier_bill_id == a_bill)
            )
        )
        .scalars()
        .all()
    )
    assert all(line.tenant_id == a.tenant_id for line in a_lines)


async def test_already_claimed_bill_is_skipped(db_session: AsyncSession) -> None:
    """A bill not in `uploaded` (e.g. already processing) is not re-claimed."""
    seed = await _seed(db_session)
    bill_id = await _upload(db_session, seed)
    # Move it to ocr_processing out of band; the worker's claimable query won't see
    # it, and even a direct _process_one skips a non-uploaded bill.
    await SupplierBillService(db_session).mark_processing(tenant_id=seed.tenant_id, bill_id=bill_id)

    processed = await _processor(db_session).run_once()
    assert processed == 0  # nothing claimable
    assert await _status(db_session, bill_id) == "ocr_processing"
