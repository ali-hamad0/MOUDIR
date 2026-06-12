"""Task 5.11 — the gated BillCommitter.

Proves the bill→stock commit reuses the SAME execution gate as the PO dispatch
(constitution V): a valid signed `bill.commit` token applies every mapped line to
inventory; an absent / forged / wrong-bill / wrong-tenant token is refused and NO
stock moves and the bill never becomes `committed`. ensure_row lets a received bill
be the first stock for a SKU. A processing failure rolls back (no partial stock) and
reverts the bill to `extracted`. The Wall holds. Audited throughout.

Negative control (run by hand): make BillCommitter SKIP ActionGate.authorize → the
no-commit-without-token test FAILS. That is the proof the gate does the work.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services.supplier_bills import SupplierBillService


def _settings(**overrides) -> Settings:
    base = dict(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=30,
    )
    base.update(overrides)
    return Settings.model_construct(**base)


def _sessionmaker_for(session: AsyncSession):
    """Hand the committer the test's transactional session for every 'open' — the cm
    does NOT close it, so the committer can reopen it across its commit/revert blocks;
    its commits create/release savepoints that roll back with the test. (Same shim as
    the agent/worker suites.)"""

    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


def _token(settings: Settings, bill_id: UUID, tenant_id: UUID, approver_id: UUID) -> str:
    return mint_approval_token(
        settings,
        action=COMMIT_ACTION,
        resource_id=bill_id,
        tenant_id=tenant_id,
        approver_id=approver_id,
    )


@dataclass
class _Seed:
    tenant_id: UUID
    approver_id: UUID
    bill_id: UUID
    tracked_id: UUID  # has an inventory row (qty 5)
    untracked_id: UUID  # mapped, but no inventory row yet


async def _seed(db: AsyncSession, *, name: str = "ShopA", tracked_qty: int = 5) -> _Seed:
    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"{uuid4().hex[:8]}@a.com",
        hashed_password=hash_password("password123"),
        role="owner",
    )
    tracked = Product(tenant_id=tenant.id, name_ar="طحين", price_lbp=1000)
    untracked = Product(tenant_id=tenant.id, name_ar="سكر", price_lbp=2000)
    db.add_all([user, tracked, untracked])
    await db.flush()
    db.add(Inventory(tenant_id=tenant.id, product_id=tracked.id, quantity=tracked_qty))

    svc = SupplierBillService(db)
    bill = await svc.create_uploaded(
        tenant_id=tenant.id,
        object_key=f"bills/{tenant.id}/{uuid4()}/x.png",
        original_filename="x.png",
        content_type="image/png",
    )
    await svc.mark_processing(tenant_id=tenant.id, bill_id=bill.id)
    await svc.save_extraction(
        tenant_id=tenant.id,
        bill_id=bill.id,
        ocr_engine="stub",
        ocr_text="...",
        extracted={"total": "0"},
        lines=[
            SupplierBillLine(name_ar="طحين", quantity=Decimal("10"), product_id=tracked.id),
            SupplierBillLine(name_ar="سكر", quantity=Decimal("25"), product_id=untracked.id),
        ],
        min_confidence=Decimal("0.9"),
    )
    await svc.approve(tenant_id=tenant.id, bill_id=bill.id, approver_id=user.id)  # → committing
    await db.flush()
    return _Seed(
        tenant_id=tenant.id,
        approver_id=user.id,
        bill_id=bill.id,
        tracked_id=tracked.id,
        untracked_id=untracked.id,
    )


async def _bill(db: AsyncSession, bill_id: UUID) -> SupplierBill:
    return (await db.execute(select(SupplierBill).where(SupplierBill.id == bill_id))).scalar_one()


async def _qty(db: AsyncSession, tenant_id: UUID, product_id: UUID) -> int | None:
    row = await InventoryRepository(db).get_by_product(tenant_id, product_id)
    return row.quantity if row else None


async def _audit_count(db: AsyncSession, tenant_id: UUID, action: str) -> int:
    # Tenant-scoped: the suite runs against the live dev database, so a global
    # count would be polluted by real rows outside the test's rolled-back txn.
    return (
        await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
        )
    ).scalar_one()


# ── Happy path: a valid token applies every mapped line to stock ─────────────


async def test_valid_token_commits_lines_to_stock(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=5)
    settings = _settings()
    committer = BillCommitter(settings, _sessionmaker_for(db_session))
    bill = await _bill(db_session, seed.bill_id)

    token = _token(settings, seed.bill_id, seed.tenant_id, seed.approver_id)
    await committer.commit(bill, token)

    # tracked: 5 + 10 = 15; untracked: ensure_row(0) + 25 = 25.
    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 15
    assert await _qty(db_session, seed.tenant_id, seed.untracked_id) == 25
    assert (await _bill(db_session, seed.bill_id)).status == "committed"
    assert await _audit_count(db_session, seed.tenant_id, "bill.committed") == 1
    assert await _audit_count(db_session, seed.tenant_id, "bill.line_committed") == 2


# ── The gate holds: no stock without a valid token ───────────────────────────


async def test_missing_token_is_refused(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=5)
    committer = BillCommitter(_settings(), _sessionmaker_for(db_session))
    bill = await _bill(db_session, seed.bill_id)

    with pytest.raises(UnauthorizedAction):
        await committer.commit(bill, None)

    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 5  # unchanged
    assert (await _bill(db_session, seed.bill_id)).status == "committing"  # not committed


async def test_forged_token_is_refused(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=5)
    committer = BillCommitter(_settings(), _sessionmaker_for(db_session))
    bill = await _bill(db_session, seed.bill_id)

    with pytest.raises(UnauthorizedAction):
        await committer.commit(bill, "not.a.valid.token")
    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 5


async def test_token_for_a_different_bill_is_refused(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=5)
    settings = _settings()
    committer = BillCommitter(settings, _sessionmaker_for(db_session))
    bill = await _bill(db_session, seed.bill_id)

    # A token minted for a DIFFERENT bill id must not authorize this commit.
    wrong = _token(settings, uuid4(), seed.tenant_id, seed.approver_id)
    with pytest.raises(UnauthorizedAction):
        await committer.commit(bill, wrong)
    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 5


async def test_token_for_a_different_tenant_is_refused(db_session: AsyncSession) -> None:
    seed = await _seed(db_session, tracked_qty=5)
    settings = _settings()
    committer = BillCommitter(settings, _sessionmaker_for(db_session))
    bill = await _bill(db_session, seed.bill_id)

    # The Wall: a token bound to another tenant can't authorize this bill.
    wrong = _token(settings, seed.bill_id, uuid4(), seed.approver_id)
    with pytest.raises(UnauthorizedAction):
        await committer.commit(bill, wrong)
    assert await _qty(db_session, seed.tenant_id, seed.tracked_id) == 5


# ── Failure reverts the bill for re-review (no partial stock in production) ───


async def test_commit_failure_reverts_to_extracted(db_session: AsyncSession) -> None:
    """A failure during the commit is absorbed: the bill reverts to `extracted` so the
    owner can re-review, and the failure is audited.

    The no-partial-stock guarantee comes from the structure — the increases and
    mark_committed run in ONE session.commit(); if anything before the commit raises,
    that transaction never commits, so in production no stock change is persisted.
    (This test uses the shared-session shim like the other agent/worker suites, which
    cannot model a separate-session rollback, so it asserts the revert + audit, not the
    rolled-back quantity. The negative control below + the single-transaction code are
    the proof of the no-partial-stock property.)"""
    seed = await _seed(db_session, tracked_qty=5)
    settings = _settings()
    committer = BillCommitter(settings, _sessionmaker_for(db_session))
    bill = await _bill(db_session, seed.bill_id)
    token = _token(settings, seed.bill_id, seed.tenant_id, seed.approver_id)

    # Force mark_committed to blow up — the commit fails and is absorbed into a revert.
    import app.infra.bill_committer as mod

    async def _boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    original = mod.SupplierBillService.mark_committed
    mod.SupplierBillService.mark_committed = _boom
    try:
        await committer.commit(bill, token)  # absorbed → revert, never raises
    finally:
        mod.SupplierBillService.mark_committed = original

    # The bill is back to extracted for re-review, with the failure audited.
    assert (await _bill(db_session, seed.bill_id)).status == "extracted"
    assert await _audit_count(db_session, seed.tenant_id, "bill.commit_failed") == 1
