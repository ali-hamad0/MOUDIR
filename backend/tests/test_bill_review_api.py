"""Task 5.12 — the bill review API.

Proves the review screen + the HIL approve flow: GET returns the bill with a
presigned image URL and its lines; PUT edits/maps lines (and only on an `extracted`
bill); approve refuses an unmapped quantitied line (422), otherwise flips the bill to
`committing`, mints a VALID signed `bill.commit` token, and schedules the commit as a
BACKGROUND task (never inline); reject requires a reason. Every route is tenant-scoped
(the Wall). Handlers are called directly with the transactional db_session and a real
User, faking Request (app.state.storage + bill_committer) and BackgroundTasks — like
the approvals-API suite.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bills import (
    approve_bill,
    get_bill,
    reject_bill,
    update_bill_lines,
)
from app.api.schemas.bills import (
    ApproveBillRequest,
    BillLinesUpdate,
    BillLineUpdate,
    RejectBillRequest,
)
from app.db.models import Product, SupplierBill, SupplierBillLine, Tenant, User
from app.infra.action_gate import ActionGate
from app.infra.bill_committer import COMMIT_ACTION
from app.infra.security import hash_password
from app.infra.settings import Settings
from app.repositories.users import UserRepository
from app.services.supplier_bills import SupplierBillService


def _settings() -> Settings:
    return Settings.model_construct(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=30,
        bill_image_url_ttl_minutes=15,
    )


class _SpyStorage:
    """Returns a fixed presigned URL instead of hitting MinIO."""

    async def presigned_get(self, key: str, ttl: timedelta) -> str:
        return f"https://minio.local/{key}?sig=test"


class _SpyCommitter:
    """Captures commit(bill, token) calls instead of moving stock."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def commit(self, bill, token) -> None:
        self.calls.append((bill, token))


def _fake_request(committer: _SpyCommitter | None = None):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                storage=_SpyStorage(),
                bill_committer=committer or _SpyCommitter(),
            )
        )
    )


@dataclass
class _Seed:
    tenant_id: UUID
    user_id: UUID
    flour_id: UUID
    sugar_id: UUID


async def _seed(db: AsyncSession, *, name: str = "ShopA") -> _Seed:
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
    return _Seed(tenant_id=tenant.id, user_id=user.id, flour_id=flour.id, sugar_id=sugar.id)


async def _user(db: AsyncSession, seed: _Seed) -> User:
    user = await UserRepository(db).get(seed.tenant_id, seed.user_id)
    assert user is not None
    return user


async def _extracted_bill(db: AsyncSession, seed: _Seed, *, map_flour: bool = True) -> UUID:
    """An `extracted` bill with two lines: طحين (optionally mapped) and an unmapped
    سكر-like line."""
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
            SupplierBillLine(
                name_ar="طحين",
                quantity=Decimal("10"),
                confidence=Decimal("0.9"),
                product_id=seed.flour_id if map_flour else None,
            ),
            SupplierBillLine(name_ar="سكر أبيض", quantity=Decimal("25"), confidence=Decimal("0.4")),
        ],
        min_confidence=Decimal("0.4"),
    )
    await db.flush()
    return bill.id


async def _status(db: AsyncSession, bill_id: UUID) -> str:
    return (
        await db.execute(select(SupplierBill.status).where(SupplierBill.id == bill_id))
    ).scalar_one()


# ── GET /bills/{id}: detail with image URL + lines ───────────────────────────


async def test_get_bill_returns_detail_with_image_and_lines(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _extracted_bill(db_session, seed)

    detail = await get_bill(
        bill_id=bill_id,
        request=_fake_request(),
        user=await _user(db_session, seed),
        db=db_session,
        settings=_settings(),
    )
    assert detail.image_url is not None and "sig=test" in detail.image_url
    assert len(detail.lines) == 2
    flour = next(line for line in detail.lines if line.name_ar == "طحين")
    assert flour.product_id == seed.flour_id
    assert flour.product_name_ar == "طحين"


async def test_get_bill_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    a_bill = await _extracted_bill(db_session, a)
    # Tenant B asking for A's bill → 404 (the Wall).
    with pytest.raises(HTTPException) as exc:
        await get_bill(
            bill_id=a_bill,
            request=_fake_request(),
            user=await _user(db_session, b),
            db=db_session,
            settings=_settings(),
        )
    assert exc.value.status_code == 404


# ── PUT /bills/{id}/lines: edit + map ────────────────────────────────────────


async def test_update_lines_maps_a_product(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _extracted_bill(db_session, seed)
    detail = await get_bill(
        bill_id=bill_id,
        request=_fake_request(),
        user=await _user(db_session, seed),
        db=db_session,
        settings=_settings(),
    )
    sugar_line = next(line for line in detail.lines if line.name_ar == "سكر أبيض")
    assert sugar_line.product_id is None

    updated = await update_bill_lines(
        bill_id=bill_id,
        payload=BillLinesUpdate(
            lines=[
                BillLineUpdate(id=sugar_line.id, product_id=seed.sugar_id, quantity=Decimal("30"))
            ]
        ),
        request=_fake_request(),
        user=await _user(db_session, seed),
        db=db_session,
        settings=_settings(),
    )
    new_sugar = next(line for line in updated.lines if line.id == sugar_line.id)
    assert new_sugar.product_id == seed.sugar_id
    assert new_sugar.quantity == Decimal("30")


# ── POST approve: mapping gate, token, background commit ──────────────────────


async def test_approve_refuses_unmapped_line(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    # سكر line stays unmapped → approve must 422.
    bill_id = await _extracted_bill(db_session, seed)
    with pytest.raises(HTTPException) as exc:
        await approve_bill(
            bill_id=bill_id,
            payload=ApproveBillRequest(),
            request=_fake_request(),
            user=await _user(db_session, seed),
            db=db_session,
            settings=_settings(),
            background=BackgroundTasks(),
        )
    assert exc.value.status_code == 422
    assert await _status(db_session, bill_id) == "extracted"  # not approved


async def test_approve_mints_token_and_schedules_commit(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _extracted_bill(db_session, seed)
    # Map the سكر line so all quantitied lines are mapped.
    user = await _user(db_session, seed)
    detail = await get_bill(
        bill_id=bill_id, request=_fake_request(), user=user, db=db_session, settings=_settings()
    )
    sugar_line = next(line for line in detail.lines if line.name_ar == "سكر أبيض")
    await update_bill_lines(
        bill_id=bill_id,
        payload=BillLinesUpdate(lines=[BillLineUpdate(id=sugar_line.id, product_id=seed.sugar_id)]),
        request=_fake_request(),
        user=user,
        db=db_session,
        settings=_settings(),
    )

    settings = _settings()
    spy = _SpyCommitter()
    background = BackgroundTasks()
    detail = await approve_bill(
        bill_id=bill_id,
        payload=ApproveBillRequest(),
        request=_fake_request(spy),
        user=user,
        db=db_session,
        settings=settings,
        background=background,
    )

    assert detail.status == "committing"
    assert await _status(db_session, bill_id) == "committing"
    # Commit was SCHEDULED, not run inline.
    assert spy.calls == []
    assert len(background.tasks) == 1

    # Run the task and prove the token the REAL gate accepts for THIS bill.
    await background()
    assert len(spy.calls) == 1
    bill_arg, token = spy.calls[0]
    assert bill_arg.id == bill_id
    approved = ActionGate.authorize(
        settings, token, action=COMMIT_ACTION, resource_id=bill_id, tenant_id=seed.tenant_id
    )
    assert approved.approver_id == seed.user_id


# ── POST reject ──────────────────────────────────────────────────────────────


async def test_reject_records_reason(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _extracted_bill(db_session, seed)
    detail = await reject_bill(
        bill_id=bill_id,
        payload=RejectBillRequest(reason="صورة مش واضحة"),
        request=_fake_request(),
        user=await _user(db_session, seed),
        db=db_session,
        settings=_settings(),
    )
    assert detail.status == "rejected"
    assert detail.reject_reason == "صورة مش واضحة"


async def test_reject_a_committed_bill_is_409(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bill_id = await _extracted_bill(db_session, seed)
    user = await _user(db_session, seed)
    # Drive it to committing then committed (via the service, simulating a finished
    # commit) so reject is an invalid transition.
    svc = SupplierBillService(db_session)
    await svc.approve(tenant_id=seed.tenant_id, bill_id=bill_id, approver_id=user.id)
    await svc.mark_committed(tenant_id=seed.tenant_id, bill_id=bill_id)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await reject_bill(
            bill_id=bill_id,
            payload=RejectBillRequest(reason="late"),
            request=_fake_request(),
            user=user,
            db=db_session,
            settings=_settings(),
        )
    assert exc.value.status_code == 409
