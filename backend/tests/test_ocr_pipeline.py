"""Task 5.9 — OCR pipeline integration + the storage Wall.

Ties the OCR pieces together (storage key → worker → extracted) and proves the two
properties the pipeline must guarantee:

  - The Wall on object storage: a bill image key is tenant-prefixed, so tenant A's
    key can never be constructed or read under tenant B (constitution I). This is the
    test the plan says to confirm by weakening object_key — dropping the tenant
    prefix makes `test_storage_key_is_tenant_scoped` fail.
  - The end-to-end flow: an uploaded bill is OCR'd (stub) + extracted to `extracted`
    with per-line confidence and a min_confidence, and the review-threshold flags the
    low-confidence line; a degraded extraction lands the bill in `ocr_failed`; a
    lifecycle transition is guarded.

OCR is the stub and the LLM is faked — the suite stays offline (CI-safe), like the
existing agent/worker suites.
"""

import io
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.ocr.agent import BillExtractionAgent
from app.agents.ocr.schemas import BillData, BillLineData
from app.db.models import Product, SupplierBill, SupplierBillLine, Tenant
from app.infra.ocr import StubOCREngine, confidence
from app.infra.settings import Settings
from app.infra.storage import StorageClient
from app.services.supplier_bills import InvalidBillTransition, SupplierBillService
from app.worker import BillProcessor


# ---- fakes (same shapes as test_worker / test_bill_agent) ----
class _FakeStructured:
    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)

    async def ainvoke(self, messages):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeModel:
    def __init__(self, outcomes):
        self._s = _FakeStructured(outcomes)

    def with_structured_output(self, schema):
        return self._s


class _FakeRouter:
    def __init__(self, outcomes):
        self._m = _FakeModel(outcomes)

    def tier1(self):
        return self._m

    def tier2(self):
        return self._m


class _FakeStorage:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def get_bytes(self, key: str) -> bytes:
        if self._fail:
            raise RuntimeError("storage down")
        buf = io.BytesIO()
        Image.new("L", (200, 120), color=255).save(buf, format="PNG")
        return buf.getvalue()


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


def _bill_data() -> BillData:
    """flour (high certainty) + oil (low certainty → flagged for review)."""
    return BillData(
        currency="LBP",
        total_amount=Decimal("1900000"),
        certainty=0.9,
        lines=[
            BillLineData(name_ar="طحين", quantity=Decimal("50"), certainty=0.95),
            BillLineData(name_ar="زيت", quantity=Decimal("12"), certainty=0.3),
        ],
    )


def _processor(session, *, outcomes=None, storage_fail=False):
    settings = _settings()
    sm = _sessionmaker_for(session)
    agent = BillExtractionAgent(_FakeRouter(outcomes or [_bill_data()]), settings, sm)
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


async def _seed(db: AsyncSession, *, name: str = "ShopA") -> _Seed:
    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    flour = Product(tenant_id=tenant.id, name_ar="طحين", price_lbp=1000)
    db.add(flour)
    await db.flush()
    return _Seed(tenant_id=tenant.id, flour_id=flour.id)


async def _upload(db: AsyncSession, seed: _Seed) -> UUID:
    key = StorageClient.object_key(seed.tenant_id, uuid4(), "فاتورة.png")
    bill = await SupplierBillService(db).create_uploaded(
        tenant_id=seed.tenant_id,
        object_key=key,
        original_filename="فاتورة.png",
        content_type="image/png",
    )
    return bill.id


# ── The Wall on object storage ───────────────────────────────────────────────


def test_storage_key_is_tenant_scoped() -> None:
    """A bill image key is tenant-prefixed, so tenant A's key is structurally
    unreachable as tenant B. (Drop the tenant prefix in object_key and this fails —
    the proof the storage Wall is real.)"""
    tenant_a = uuid4()
    tenant_b = uuid4()
    bill_id = uuid4()

    key_a = StorageClient.object_key(tenant_a, bill_id, "x.png")
    assert key_a.startswith(f"bills/{tenant_a}/")
    # The same bill id under B yields a DIFFERENT key — no overlap with A's namespace.
    key_b = StorageClient.object_key(tenant_b, bill_id, "x.png")
    assert key_b.startswith(f"bills/{tenant_b}/")
    assert key_a != key_b
    # A's tenant id never appears in B's key (and vice versa).
    assert str(tenant_a) not in key_b
    assert str(tenant_b) not in key_a


# ── End-to-end: uploaded → extracted, with confidence + threshold flagging ───


async def test_pipeline_extracts_with_confidence_and_flag(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _upload(db_session, seed)

    processed = await _processor(db_session).run_once()
    assert processed == 1

    bill = (
        await db_session.execute(select(SupplierBill).where(SupplierBill.id == bill_id))
    ).scalar_one()
    assert bill.status == "extracted"
    assert bill.min_confidence is not None

    lines = (
        (
            await db_session.execute(
                select(SupplierBillLine).where(SupplierBillLine.supplier_bill_id == bill_id)
            )
        )
        .scalars()
        .all()
    )
    by_name = {line.name_ar: line for line in lines}
    # Stub OCR confidence is 0.95; the low-certainty زيت line (0.3) → 0.285, which is
    # below the review threshold and must be flagged.
    threshold = _settings().ocr_confidence_review_threshold
    assert confidence.needs_review(float(by_name["زيت"].confidence), threshold) is True
    # The high-certainty طحين line (0.95×0.95≈0.9) is NOT flagged.
    assert confidence.needs_review(float(by_name["طحين"].confidence), threshold) is False


# ── Degrade path: a broken extraction lands in ocr_failed ────────────────────


async def test_pipeline_failure_marks_ocr_failed(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _upload(db_session, seed)

    # Storage read fails → the whole pipeline degrades the bill to ocr_failed.
    await _processor(db_session, storage_fail=True).run_once()

    bill = (
        await db_session.execute(select(SupplierBill).where(SupplierBill.id == bill_id))
    ).scalar_one()
    assert bill.status == "ocr_failed"


# ── Lifecycle is guarded ─────────────────────────────────────────────────────


async def test_uploaded_bill_cannot_be_approved(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _upload(db_session, seed)  # still `uploaded`
    with pytest.raises(InvalidBillTransition):
        await SupplierBillService(db_session).approve(
            tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=uuid4()
        )
