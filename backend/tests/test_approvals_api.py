"""Task 4.12 — the owner's HIL approvals inbox API.

Proves the inbox is tenant-scoped (the Wall), the three owner decisions walk the
PO lifecycle and are audited, and — the crux — approve mints a VALID signed
dispatch token and schedules the supplier send as a BACKGROUND task (never inline,
so a slow supplier can't hang the approve). reject requires a reason; mark-sent
closes a dispatch_failed PO.

Following the suite's harness, route handlers are called directly with the
transactional db_session and a real User — the routes have no tenant_id parameter
to spoof; scope comes from the user. The approve route also needs a Request (for
app.state.supplier_dispatcher) and BackgroundTasks; both are faked here so we can
inspect what was scheduled WITHOUT running a real dispatch.
"""

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.approvals import (
    approve_purchase_order,
    list_approvals,
    mark_purchase_order_sent,
    reject_purchase_order,
)
from app.api.schemas.approvals import ApproveRequest, RejectRequest
from app.db.models import AuditLog, Product, PurchaseOrder, Supplier, Tenant, User
from app.infra.action_gate import ActionGate
from app.infra.security import hash_password
from app.infra.settings import Settings
from app.infra.supplier_dispatch import DISPATCH_ACTION
from app.repositories.users import UserRepository
from app.services.purchase_orders import PurchaseOrderService


def _settings() -> Settings:
    """Crypto-capable settings, built like test_action_gate.py."""
    return Settings.model_construct(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=30,
    )


class _SpyDispatcher:
    """Captures dispatch(po, supplier, token) calls instead of sending."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def dispatch(self, po, supplier, token) -> None:
        self.calls.append((po, supplier, token))


def _fake_request(dispatcher: _SpyDispatcher):
    """A stand-in Request exposing only app.state.supplier_dispatcher."""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(supplier_dispatcher=dispatcher))
    )


@dataclass
class _Seed:
    tenant_id: UUID
    user_id: UUID
    product_id: UUID
    supplier_id: UUID


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
    product = Product(tenant_id=tenant.id, name_ar="كعك", name_en="Kaak", price_lbp=1000)
    supplier = Supplier(tenant_id=tenant.id, name="مورّد", webhook_url="https://supplier/hook")
    db.add_all([user, product, supplier])
    await db.flush()
    return _Seed(
        tenant_id=tenant.id, user_id=user.id, product_id=product.id, supplier_id=supplier.id
    )


async def _user(db: AsyncSession, seed: _Seed) -> User:
    user = await UserRepository(db).get(seed.tenant_id, seed.user_id)
    assert user is not None
    return user


async def _draft(db: AsyncSession, seed: _Seed) -> UUID:
    po = await PurchaseOrderService(db).draft(
        tenant_id=seed.tenant_id,
        product_id=seed.product_id,
        supplier_id=seed.supplier_id,
        quantity=10,
        reason="crossed reorder threshold",
        agent_note_ar="بدنا نطلب كمان",
    )
    return po.id


async def _status(db: AsyncSession, po_id: UUID) -> str:
    return (
        await db.execute(select(PurchaseOrder.status).where(PurchaseOrder.id == po_id))
    ).scalar_one()


async def _audit_count(db: AsyncSession, action: str, po_id: UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == action, AuditLog.target == str(po_id))
            )
        ).scalar_one()
    )


# ── GET /approvals: tenant-scoped inbox ──────────────────────────────────────


async def test_inbox_lists_only_this_tenants_drafts(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    a_draft = await _draft(db_session, a)
    b_draft = await _draft(db_session, b)

    page = await list_approvals(user=await _user(db_session, a), db=db_session, limit=50, offset=0)
    ids = {item.id for item in page.items}
    assert a_draft in ids
    assert b_draft not in ids  # the Wall
    assert page.total == 1
    # Joined to the catalog for display.
    assert page.items[0].product_name_ar == "كعك"
    assert page.items[0].agent_note_ar == "بدنا نطلب كمان"


# ── POST approve: walks the lifecycle, mints a VALID token, schedules dispatch ─


async def test_approve_flips_status_mints_token_and_schedules_dispatch(
    db_session: AsyncSession,
) -> None:
    seed = await _seed(db_session)
    po_id = await _draft(db_session, seed)
    settings = _settings()
    spy = _SpyDispatcher()
    background = BackgroundTasks()

    read = await approve_purchase_order(
        po_id=po_id,
        payload=ApproveRequest(note="مبروك"),
        request=_fake_request(spy),
        user=await _user(db_session, seed),
        db=db_session,
        settings=settings,
        background=background,
    )

    # Lifecycle marker flipped + audited.
    assert read.status == "approved"
    assert await _status(db_session, po_id) == "approved"
    assert await _audit_count(db_session, "po.approved", po_id) == 1

    # Dispatch was SCHEDULED (added to BackgroundTasks), not run inline — the spy
    # has not been called yet, but the task is queued against it.
    assert spy.calls == []
    assert len(background.tasks) == 1

    # Run the scheduled task and prove what was handed to the dispatcher: the PO,
    # its supplier, and a token the REAL ActionGate accepts for THIS dispatch.
    await background()
    assert len(spy.calls) == 1
    po_arg, supplier_arg, token = spy.calls[0]
    assert po_arg.id == po_id
    assert supplier_arg is not None and supplier_arg.id == seed.supplier_id
    approved = ActionGate.authorize(
        settings, token, action=DISPATCH_ACTION, resource_id=po_id, tenant_id=seed.tenant_id
    )
    assert approved.approver_id == seed.user_id


async def test_approve_a_non_draft_is_409(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    po_id = await _draft(db_session, seed)
    settings = _settings()
    user = await _user(db_session, seed)

    # First approve succeeds.
    await approve_purchase_order(
        po_id=po_id,
        payload=ApproveRequest(),
        request=_fake_request(_SpyDispatcher()),
        user=user,
        db=db_session,
        settings=settings,
        background=BackgroundTasks(),
    )
    # Second approve → invalid transition → 409.
    with pytest.raises(HTTPException) as exc:
        await approve_purchase_order(
            po_id=po_id,
            payload=ApproveRequest(),
            request=_fake_request(_SpyDispatcher()),
            user=user,
            db=db_session,
            settings=settings,
            background=BackgroundTasks(),
        )
    assert exc.value.status_code == 409


async def test_approve_foreign_tenants_po_is_404(db_session: AsyncSession) -> None:
    """The Wall: tenant B's owner cannot approve tenant A's PO (scoped → 404)."""
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    a_po = await _draft(db_session, a)

    with pytest.raises(HTTPException) as exc:
        await approve_purchase_order(
            po_id=a_po,
            payload=ApproveRequest(),
            request=_fake_request(_SpyDispatcher()),
            user=await _user(db_session, b),
            db=db_session,
            settings=_settings(),
            background=BackgroundTasks(),
        )
    assert exc.value.status_code == 404
    assert await _status(db_session, a_po) == "draft"  # untouched


# ── POST reject: reason required, provisions nothing ─────────────────────────


async def test_reject_records_reason_and_audits(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    po_id = await _draft(db_session, seed)

    read = await reject_purchase_order(
        po_id=po_id,
        payload=RejectRequest(reason="مش وقتها"),
        user=await _user(db_session, seed),
        db=db_session,
    )
    assert read.status == "rejected"
    assert read.reject_reason == "مش وقتها"
    assert await _audit_count(db_session, "po.rejected", po_id) == 1


def test_reject_without_reason_is_a_validation_error() -> None:
    """reason is required at the schema boundary → 422 before the handler runs."""
    with pytest.raises(ValueError):
        RejectRequest(reason="")  # min_length=1


# ── POST mark-sent: closes a dispatch_failed PO ──────────────────────────────


async def test_mark_sent_closes_a_failed_po(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    po_id = await _draft(db_session, seed)
    svc = PurchaseOrderService(db_session)
    await svc.approve(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id)
    await svc.mark_dispatch_failed(tenant_id=seed.tenant_id, po_id=po_id, error="webhook down")

    read = await mark_purchase_order_sent(
        po_id=po_id, user=await _user(db_session, seed), db=db_session
    )
    assert read.status == "sent"
    assert await _audit_count(db_session, "po.sent_manually", po_id) == 1


async def test_mark_sent_on_a_draft_is_409(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    po_id = await _draft(db_session, seed)  # still draft, not dispatch_failed

    with pytest.raises(HTTPException) as exc:
        await mark_purchase_order_sent(
            po_id=po_id, user=await _user(db_session, seed), db=db_session
        )
    assert exc.value.status_code == 409
