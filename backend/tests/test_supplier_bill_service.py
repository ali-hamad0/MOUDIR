"""Task 5.3 — supplier-bill repository + service.

Proves SupplierBillService is the ONLY bill-state writer and that every transition
is guarded against the current status (a bad transition is a 409-shaped domain
error, never a silent overwrite) and audited + breadcrumbed. Also proves the repo's
review listing, claimable queue, and bill-with-lines load are tenant-scoped. Token
minting + the gated stock commit live ABOVE this service (5.11/5.12).
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Product,
    SupplierBill,
    SupplierBillEvent,
    SupplierBillLine,
    Tenant,
    User,
)
from app.infra.security import hash_password
from app.repositories.supplier_bills import SupplierBillRepository
from app.services.supplier_bills import (
    InvalidBillTransition,
    SupplierBillNotFound,
    SupplierBillService,
)


@dataclass
class _Seed:
    """A tenant with an owner-user and a product. Plain UUIDs."""

    tenant_id: UUID
    user_id: UUID
    product_id: UUID


async def _seed(db: AsyncSession) -> _Seed:
    tenant = Tenant(name="ShopA", whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"{uuid4().hex[:8]}@a.com",
        hashed_password=hash_password("password123"),
        role="owner",
    )
    product = Product(tenant_id=tenant.id, name_ar="طحين", price_lbp=1000)
    db.add_all([user, product])
    await db.flush()
    return _Seed(tenant_id=tenant.id, user_id=user.id, product_id=product.id)


async def _uploaded(db: AsyncSession, seed: _Seed, *, key: str | None = None) -> UUID:
    bill = await SupplierBillService(db).create_uploaded(
        tenant_id=seed.tenant_id,
        object_key=key or f"bills/{seed.tenant_id}/{uuid4()}/فاتورة.jpg",
        original_filename="فاتورة.jpg",
        content_type="image/jpeg",
        actor_id=seed.user_id,
    )
    return bill.id


async def _to_extracted(db: AsyncSession, seed: _Seed, bill_id: UUID, *, lines=None) -> None:
    """Walk uploaded → ocr_processing → extracted with one stub line."""
    svc = SupplierBillService(db)
    await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=bill_id)
    if lines is None:
        lines = [
            SupplierBillLine(
                raw_text="طحين ٥٠ كيلو",
                name_ar="طحين",
                quantity=Decimal("50"),
                unit="kg",
                line_amount=Decimal("100.00"),
                confidence=Decimal("0.900"),
                product_id=seed.product_id,
            )
        ]
    await svc.save_extraction(
        tenant_id=seed.tenant_id,
        bill_id=bill_id,
        ocr_engine="stub",
        ocr_text="طحين ٥٠ كيلو ... المجموع ١٠٠",
        extracted={"supplier": "مورّد", "total": "100.00"},
        lines=lines,
        min_confidence=Decimal("0.900"),
        total_amount=Decimal("100.00"),
        currency="USD",
    )


async def _status(db: AsyncSession, bill_id: UUID) -> str:
    return (
        await db.execute(select(SupplierBill.status).where(SupplierBill.id == bill_id))
    ).scalar_one()


async def _audit_count(db: AsyncSession, action: str, bill_id: UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == action, AuditLog.target == str(bill_id))
        )
    ).scalar_one()


async def _event_count(db: AsyncSession, bill_id: UUID, event: str) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(SupplierBillEvent)
            .where(
                SupplierBillEvent.supplier_bill_id == bill_id,
                SupplierBillEvent.event == event,
            )
        )
    ).scalar_one()


# ── Happy path: the status machine walks the lifecycle ───────────────────────


async def test_create_uploaded_writes_event_and_audit(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _uploaded(db_session, seed)

    assert await _status(db_session, bill_id) == "uploaded"
    assert await _event_count(db_session, bill_id, "uploaded") == 1
    assert await _audit_count(db_session, "bill.uploaded", bill_id) == 1


async def test_process_extract_approve_commit_walks_the_machine(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)

    await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=bill_id)
    assert await _status(db_session, bill_id) == "ocr_processing"

    await svc.save_extraction(
        tenant_id=seed.tenant_id,
        bill_id=bill_id,
        ocr_engine="stub",
        ocr_text="...",
        extracted={"total": "100.00"},
        lines=[SupplierBillLine(name_ar="طحين", quantity=Decimal("5"), product_id=seed.product_id)],
        min_confidence=Decimal("0.880"),
        total_amount=Decimal("100.00"),
        currency="USD",
    )
    extracted = await svc._repo.get(seed.tenant_id, bill_id)
    assert extracted.status == "extracted"
    assert extracted.min_confidence == Decimal("0.880")
    assert extracted.ocr_engine == "stub"

    approved = await svc.approve(
        tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id
    )
    # Approval moves it OUT of the review list into the transient committing state.
    assert approved.status == "committing"
    assert approved.reviewed_by == seed.user_id
    assert approved.reviewed_at is not None

    committed = await svc.mark_committed(tenant_id=seed.tenant_id, bill_id=bill_id)
    assert committed.status == "committed"
    assert committed.committed_at is not None

    assert await _audit_count(db_session, "bill.extracted", bill_id) == 1
    assert await _audit_count(db_session, "bill.approved", bill_id) == 1
    assert await _audit_count(db_session, "bill.committed", bill_id) == 1


async def test_save_extraction_persists_lines_in_scope(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, bill_id)

    rows = await SupplierBillRepository(db_session).get_lines(seed.tenant_id, bill_id)
    assert len(rows) == 1
    line, product = rows[0]
    # The service forced the line into the bill's tenant/bill scope (the Wall).
    assert line.tenant_id == seed.tenant_id
    assert line.supplier_bill_id == bill_id
    assert line.product_id == seed.product_id
    assert product is not None and product.name_ar == "طحين"


async def test_ocr_failed_path(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)
    await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=bill_id)

    failed = await svc.mark_ocr_failed(
        tenant_id=seed.tenant_id, bill_id=bill_id, error="vision: no text"
    )
    assert failed.status == "ocr_failed"
    assert await _event_count(db_session, bill_id, "ocr_failed") == 1
    assert await _audit_count(db_session, "bill.ocr_failed", bill_id) == 1


async def test_reject_records_reason(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, bill_id)

    rejected = await svc.reject(
        tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id, reason="صورة مش واضحة"
    )
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "صورة مش واضحة"
    assert await _audit_count(db_session, "bill.rejected", bill_id) == 1


async def test_commit_failure_reverts_to_extracted(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, bill_id)
    await svc.approve(tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id)

    reverted = await svc.revert_to_extracted(
        tenant_id=seed.tenant_id, bill_id=bill_id, error="committer crashed"
    )
    assert reverted.status == "extracted"
    assert await _audit_count(db_session, "bill.commit_failed", bill_id) == 1


# ── Bad transitions are refused (the 409 shape) ──────────────────────────────


async def test_cannot_approve_a_non_extracted_bill(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)  # still `uploaded`

    with pytest.raises(InvalidBillTransition):
        await svc.approve(tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id)


async def test_cannot_double_approve(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, bill_id)
    await svc.approve(tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id)

    # Already `committing` — a second approve is an invalid transition (no
    # double-approve window).
    with pytest.raises(InvalidBillTransition):
        await svc.approve(tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id)


async def test_cannot_commit_an_unapproved_bill(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, bill_id)

    # extracted (not committing) → mark_committed must refuse.
    with pytest.raises(InvalidBillTransition):
        await svc.mark_committed(tenant_id=seed.tenant_id, bill_id=bill_id)


async def test_cannot_reject_a_committed_bill(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, bill_id)
    await svc.approve(tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id)
    await svc.mark_committed(tenant_id=seed.tenant_id, bill_id=bill_id)

    with pytest.raises(InvalidBillTransition):
        await svc.reject(
            tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id, reason="late"
        )


# ── The Wall: a foreign tenant cannot reach this bill ────────────────────────


async def test_foreign_tenant_cannot_load_bill(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, bill_id)
    other_tenant = uuid4()

    with pytest.raises(SupplierBillNotFound):
        await svc.approve(tenant_id=other_tenant, bill_id=bill_id, approver_id=seed.user_id)


# ── Repo: review list, claimable queue, scoping ──────────────────────────────


async def test_review_list_shows_extracted_and_failed_only(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    repo = SupplierBillRepository(db_session)

    extracted_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, extracted_id)

    failed_id = await _uploaded(db_session, seed)
    await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=failed_id)
    await svc.mark_ocr_failed(tenant_id=seed.tenant_id, bill_id=failed_id, error="x")

    # A committed bill should NOT appear in the review list.
    committed_id = await _uploaded(db_session, seed)
    await _to_extracted(db_session, seed, committed_id)
    await svc.approve(tenant_id=seed.tenant_id, bill_id=committed_id, approver_id=seed.user_id)
    await svc.mark_committed(tenant_id=seed.tenant_id, bill_id=committed_id)

    rows = await repo.list_for_review(seed.tenant_id, limit=50, offset=0)
    ids = {bill.id for bill, _supplier in rows}
    assert extracted_id in ids
    assert failed_id in ids
    assert committed_id not in ids
    assert await repo.count_for_review(seed.tenant_id) == 2


async def test_claimable_queue_lists_uploaded_only(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    repo = SupplierBillRepository(db_session)

    up1 = await _uploaded(db_session, seed)
    up2 = await _uploaded(db_session, seed)
    # One already in processing — not claimable.
    busy = await _uploaded(db_session, seed)
    await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=busy)

    claimable = await repo.list_claimable(seed.tenant_id, limit=10)
    ids = {b.id for b in claimable}
    assert ids == {up1, up2}


async def test_review_list_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _seed(db_session)
    b = await _seed(db_session)
    a_bill = await _uploaded(db_session, a)
    await _to_extracted(db_session, a, a_bill)
    b_bill = await _uploaded(db_session, b)
    await _to_extracted(db_session, b, b_bill)

    repo = SupplierBillRepository(db_session)
    a_rows = await repo.list_for_review(a.tenant_id, limit=50, offset=0)
    a_ids = {bill.id for bill, _s in a_rows}
    assert a_bill in a_ids
    assert b_bill not in a_ids
    assert await repo.count_for_review(a.tenant_id) == 1
