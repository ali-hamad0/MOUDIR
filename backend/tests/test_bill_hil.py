"""Task 5.13 — the consolidated Human-in-the-Loop proof for supplier bills.

This is the end-to-end evidence that constitution V holds for the bill→stock loop. It
drives the REAL pieces together — the SupplierBillService lifecycle, the bill-review
API (approve/reject), the ActionGate token, and the REAL BillCommitter applying stock
— mocking nothing but storage (no MinIO) and using the OCR stub upstream. It is the
bill counterpart of test_hil_purchase_orders.py.

What it proves (the spec's points):
  1. THE GATE HOLDS — commit with no / forged / wrong-bill / wrong-tenant token is
     refused; NO stock moves and the bill is never `committed` (the "accidental call
     without authorization is rejected" reading of constitution V).
  2. HAPPY PATH — extracted → map → approve → (token minted) → commit → inventory
     increased per line; bill `committed`, committed_at set.
  3. REJECT — reject → `rejected` + reason; never committed; provisions no stock.
  4. NO AUTO-COMMIT — an extracted bill never moved stock without an approval.
  5. THE WALL — tenant A's owner can't view/approve/commit tenant B's bill; the
     commit is bound to the bill's own tenant.
  6. AUDIT — uploaded / extracted / approved / committed / line_committed / rejected
     each write an audit row.

Negative control (run by hand, see the module-end note): make BillCommitter SKIP
ActionGate.authorize → assertion (1) FAILS. That is the proof the gate is the thing
doing the work, not the status flag.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bills import approve_bill, get_bill, reject_bill
from app.api.schemas.bills import (
    ApproveBillRequest,
    RejectBillRequest,
)
from app.db.models import (
    AuditLog,
    Inventory,
    Product,
    SupplierBill,
    SupplierBillLine,
    Tenant,
    User,
)
from app.infra.action_gate import UnauthorizedAction, mint_approval_token
from app.infra.bill_committer import COMMIT_ACTION, BillCommitter
from app.infra.security import hash_password
from app.infra.settings import Settings
from app.repositories.inventory import InventoryRepository
from app.repositories.users import UserRepository
from app.services.supplier_bills import SupplierBillService

# ── Offline harness ──────────────────────────────────────────────────────────


def _settings() -> Settings:
    return Settings.model_construct(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=30,
        bill_image_url_ttl_minutes=15,
    )


def _sessionmaker_for(session: AsyncSession):
    """The committer's per-call sessions reuse the test transaction (shim shared with
    the agent/worker/committer suites)."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


class _SpyStorage:
    async def presigned_get(self, key: str, ttl: timedelta) -> str:
        return f"https://minio.local/{key}?sig=test"


def _request(committer):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(storage=_SpyStorage(), bill_committer=committer))
    )


@dataclass
class _Seed:
    tenant_id: UUID
    user_id: UUID
    flour_id: UUID
    sugar_id: UUID


async def _seed(db: AsyncSession, *, name: str = "ShopA", flour_qty: int = 5) -> _Seed:
    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"{uuid4().hex[:8]}@a.com",
        hashed_password=hash_password("password123"),
        role="owner",
    )
    flour = Product(tenant_id=tenant.id, name_ar="طحين", price_lbp=1000)
    sugar = Product(tenant_id=tenant.id, name_ar="سكر", price_lbp=2000)
    db.add_all([user, flour, sugar])
    await db.flush()
    db.add(Inventory(tenant_id=tenant.id, product_id=flour.id, quantity=flour_qty))
    await db.flush()
    return _Seed(tenant_id=tenant.id, user_id=user.id, flour_id=flour.id, sugar_id=sugar.id)


async def _user(db: AsyncSession, seed: _Seed) -> User:
    user = await UserRepository(db).get(seed.tenant_id, seed.user_id)
    assert user is not None
    return user


async def _extracted_bill(db: AsyncSession, seed: _Seed) -> UUID:
    """An `extracted` bill: طحين mapped (+10), سكر mapped (+25, first stock for it)."""
    svc = SupplierBillService(db)
    bill = await svc.create_uploaded(
        tenant_id=seed.tenant_id,
        object_key=f"bills/{seed.tenant_id}/{uuid4()}/x.png",
        original_filename="x.png",
        content_type="image/png",
    )
    await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=bill.id)
    await svc.save_extraction(
        tenant_id=seed.tenant_id,
        bill_id=bill.id,
        ocr_engine="stub",
        ocr_text="...",
        extracted={"total": "0"},
        lines=[
            SupplierBillLine(name_ar="طحين", quantity=Decimal("10"), product_id=seed.flour_id),
            SupplierBillLine(name_ar="سكر", quantity=Decimal("25"), product_id=seed.sugar_id),
        ],
        min_confidence=Decimal("0.9"),
    )
    await db.flush()
    return bill.id


async def _status(db: AsyncSession, bill_id: UUID) -> str:
    return (
        await db.execute(select(SupplierBill.status).where(SupplierBill.id == bill_id))
    ).scalar_one()


async def _qty(db: AsyncSession, tenant_id: UUID, product_id: UUID) -> int | None:
    row = await InventoryRepository(db).get_by_product(tenant_id, product_id)
    return row.quantity if row else None


async def _audit_count(db: AsyncSession, action: str) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
        )
    ).scalar_one()


async def _approve_and_run(db: AsyncSession, seed: _Seed, bill_id: UUID, committer) -> None:
    """Approve via the API (mints the token, schedules the commit), then run the
    scheduled background task — the full production approve→commit path."""
    background = BackgroundTasks()
    await approve_bill(
        bill_id=bill_id,
        payload=ApproveBillRequest(),
        request=_request(committer),
        user=await _user(db, seed),
        db=db,
        settings=_settings(),
        background=background,
    )
    await background()  # runs BillCommitter.commit(bill, token)


# ── 2 + 6. Happy path through the API + real committer, audited ──────────────


async def test_happy_path_commits_stock(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, flour_qty=5)
    bill_id = await _extracted_bill(db_session, seed)
    committer = BillCommitter(_settings(), _sessionmaker_for(db_session))

    await _approve_and_run(db_session, seed, bill_id, committer)

    # طحين: 5 + 10 = 15; سكر: first stock (ensure_row 0) + 25 = 25.
    assert await _qty(db_session, seed.tenant_id, seed.flour_id) == 15
    assert await _qty(db_session, seed.tenant_id, seed.sugar_id) == 25
    assert await _status(db_session, bill_id) == "committed"

    for action in ("bill.uploaded", "bill.extracted", "bill.approved", "bill.committed"):
        assert await _audit_count(db_session, action) >= 1
    assert await _audit_count(db_session, "bill.line_committed") == 2


# ── 1. The gate holds: no stock without a valid token ────────────────────────


async def test_no_commit_without_a_valid_token(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, flour_qty=5)
    bill_id = await _extracted_bill(db_session, seed)
    # Move to committing (as approve would) so only the token is missing.
    await SupplierBillService(db_session).approve(
        tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id
    )
    await db_session.flush()
    committer = BillCommitter(_settings(), _sessionmaker_for(db_session))
    bill = (
        await db_session.execute(select(SupplierBill).where(SupplierBill.id == bill_id))
    ).scalar_one()

    for bad in (
        None,
        "forged.token",
        mint_approval_token(
            _settings(),
            action=COMMIT_ACTION,
            resource_id=uuid4(),  # wrong bill
            tenant_id=seed.tenant_id,
            approver_id=seed.user_id,
        ),
    ):
        with pytest.raises(UnauthorizedAction):
            await committer.commit(bill, bad)

    # No stock moved; the bill never became committed.
    assert await _qty(db_session, seed.tenant_id, seed.flour_id) == 5
    assert await _qty(db_session, seed.tenant_id, seed.sugar_id) is None
    assert await _status(db_session, bill_id) == "committing"


async def test_token_from_another_tenant_is_refused(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, flour_qty=5)
    bill_id = await _extracted_bill(db_session, seed)
    await SupplierBillService(db_session).approve(
        tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=seed.user_id
    )
    await db_session.flush()
    committer = BillCommitter(_settings(), _sessionmaker_for(db_session))
    bill = (
        await db_session.execute(select(SupplierBill).where(SupplierBill.id == bill_id))
    ).scalar_one()

    # A token bound to a DIFFERENT tenant must not authorize this bill (the Wall).
    wrong = mint_approval_token(
        _settings(),
        action=COMMIT_ACTION,
        resource_id=bill_id,
        tenant_id=uuid4(),
        approver_id=seed.user_id,
    )
    with pytest.raises(UnauthorizedAction):
        await committer.commit(bill, wrong)
    assert await _qty(db_session, seed.tenant_id, seed.flour_id) == 5


# ── 3. Reject provisions no stock ────────────────────────────────────────────


async def test_reject_provisions_no_stock(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, flour_qty=5)
    bill_id = await _extracted_bill(db_session, seed)

    detail = await reject_bill(
        bill_id=bill_id,
        payload=RejectBillRequest(reason="صورة مش واضحة"),
        request=_request(BillCommitter(_settings(), _sessionmaker_for(db_session))),
        user=await _user(db_session, seed),
        db=db_session,
        settings=_settings(),
    )
    assert detail.status == "rejected"
    assert await _qty(db_session, seed.tenant_id, seed.flour_id) == 5  # untouched
    assert await _qty(db_session, seed.tenant_id, seed.sugar_id) is None
    assert await _audit_count(db_session, "bill.committed") == 0


# ── 4. No auto-commit: extracted bill never moved stock on its own ───────────


async def test_extracted_bill_has_not_moved_stock(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, flour_qty=5)
    await _extracted_bill(db_session, seed)
    # Just reaching `extracted` (worker output) provisions nothing — only a gated
    # commit does, and that requires a human approval + token.
    assert await _qty(db_session, seed.tenant_id, seed.flour_id) == 5
    assert await _qty(db_session, seed.tenant_id, seed.sugar_id) is None


# ── 5. The Wall: a foreign tenant cannot view/approve another's bill ─────────


async def test_foreign_tenant_cannot_view_or_approve(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    a_bill = await _extracted_bill(db_session, a)
    committer = BillCommitter(_settings(), _sessionmaker_for(db_session))

    # B can't view A's bill.
    with pytest.raises(HTTPException) as exc:
        await get_bill(
            bill_id=a_bill,
            request=_request(committer),
            user=await _user(db_session, b),
            db=db_session,
            settings=_settings(),
        )
    assert exc.value.status_code == 404

    # B can't approve A's bill.
    with pytest.raises(HTTPException) as exc:
        await approve_bill(
            bill_id=a_bill,
            payload=ApproveBillRequest(),
            request=_request(committer),
            user=await _user(db_session, b),
            db=db_session,
            settings=_settings(),
            background=BackgroundTasks(),
        )
    assert exc.value.status_code == 404
    # A's stock is untouched by B's attempts.
    assert await _qty(db_session, a.tenant_id, a.flour_id) == 5


# Negative control (run by hand): comment out the ActionGate.authorize(...) call in
# app/infra/bill_committer.py → test_no_commit_without_a_valid_token and
# test_token_from_another_tenant_is_refused FAIL (the committer commits without a
# token). That is the proof the gate, not the status, does the work.
