"""Task 4.7 — purchase-order repository + service.

Proves PurchaseOrderService is the ONLY PO-state writer and that every transition
is guarded against the current status (a bad transition is a 409-shaped domain
error, never a silent overwrite) and audited. Also proves the repo's
has_open_po_for_product (idempotent drafting, 4.9) and the tenant-scoped inbox
listing. Token minting + dispatch live above this service (4.10/4.12).
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Product,
    PurchaseOrder,
    PurchaseOrderEvent,
    Supplier,
    Tenant,
    User,
)
from app.infra.security import hash_password
from app.repositories.purchase_orders import PurchaseOrderRepository
from app.services.purchase_orders import (
    InvalidPurchaseOrderTransition,
    PurchaseOrderNotFound,
    PurchaseOrderService,
)


@dataclass
class _Seed:
    """A tenant with an owner-user, a product, and a supplier. Plain UUIDs."""

    tenant_id: UUID
    user_id: UUID
    product_id: UUID
    supplier_id: UUID


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
    product = Product(tenant_id=tenant.id, name_ar="كعك", price_lbp=1000)
    supplier = Supplier(tenant_id=tenant.id, name="مورّد", webhook_url="https://x/y")
    db.add_all([user, product, supplier])
    await db.flush()
    return _Seed(
        tenant_id=tenant.id,
        user_id=user.id,
        product_id=product.id,
        supplier_id=supplier.id,
    )


async def _draft(db: AsyncSession, seed: _Seed, *, qty: int = 10) -> UUID:
    po = await PurchaseOrderService(db).draft(
        tenant_id=seed.tenant_id,
        product_id=seed.product_id,
        supplier_id=seed.supplier_id,
        quantity=qty,
        reason="crossed reorder threshold",
        agent_note_ar="بدنا نطلب كمان",
    )
    return po.id


async def _status(db: AsyncSession, po_id: UUID) -> str:
    return (
        await db.execute(select(PurchaseOrder.status).where(PurchaseOrder.id == po_id))
    ).scalar_one()


async def _audit_count(db: AsyncSession, action: str, po_id: UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == action, AuditLog.target == str(po_id))
        )
    ).scalar_one()


async def _event_count(db: AsyncSession, po_id: UUID, event: str) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(PurchaseOrderEvent)
            .where(
                PurchaseOrderEvent.purchase_order_id == po_id,
                PurchaseOrderEvent.event == event,
            )
        )
    ).scalar_one()


# ── Happy path: the status machine walks draft → approved → sent ─────────────


async def test_draft_writes_draft_event_and_audit(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    po_id = await _draft(db_session, seed)

    assert await _status(db_session, po_id) == "draft"
    assert await _event_count(db_session, po_id, "drafted") == 1
    assert await _audit_count(db_session, "po.drafted", po_id) == 1


async def test_draft_approve_dispatch_walks_the_machine(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    po_id = await _draft(db_session, seed)

    approved = await svc.approve(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id)
    assert approved.status == "approved"
    assert approved.reviewed_by == seed.user_id
    assert approved.reviewed_at is not None

    sent = await svc.mark_dispatched(tenant_id=seed.tenant_id, po_id=po_id)
    assert sent.status == "sent"
    assert sent.dispatched_at is not None
    assert sent.dispatch_attempts == 1

    assert await _audit_count(db_session, "po.approved", po_id) == 1
    assert await _audit_count(db_session, "po.sent", po_id) == 1


async def test_reject_requires_status_draft_and_records_reason(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    po_id = await _draft(db_session, seed)

    rejected = await svc.reject(
        tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id, reason="مش لازم هلق"
    )
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "مش لازم هلق"
    assert await _audit_count(db_session, "po.rejected", po_id) == 1


async def test_dispatch_failed_then_manual_send(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    po_id = await _draft(db_session, seed)
    await svc.approve(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id)

    failed = await svc.mark_dispatch_failed(
        tenant_id=seed.tenant_id, po_id=po_id, error="webhook 500"
    )
    assert failed.status == "dispatch_failed"
    assert failed.dispatch_error == "webhook 500"

    closed = await svc.mark_sent_manually(
        tenant_id=seed.tenant_id, po_id=po_id, actor_id=seed.user_id
    )
    assert closed.status == "sent"
    assert await _audit_count(db_session, "po.dispatch_failed", po_id) == 1
    assert await _audit_count(db_session, "po.sent_manually", po_id) == 1


# ── Bad transitions are refused (the 409 shape) ──────────────────────────────


async def test_cannot_approve_a_non_draft(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    po_id = await _draft(db_session, seed)
    await svc.approve(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id)

    # Approving an already-approved PO is an invalid transition.
    with pytest.raises(InvalidPurchaseOrderTransition):
        await svc.approve(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id)


async def test_cannot_reject_a_sent_po(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    po_id = await _draft(db_session, seed)
    await svc.approve(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id)
    await svc.mark_dispatched(tenant_id=seed.tenant_id, po_id=po_id)

    with pytest.raises(InvalidPurchaseOrderTransition):
        await svc.reject(
            tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id, reason="late"
        )


async def test_cannot_mark_dispatched_a_draft(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    po_id = await _draft(db_session, seed)

    with pytest.raises(InvalidPurchaseOrderTransition):
        await svc.mark_dispatched(tenant_id=seed.tenant_id, po_id=po_id)


# ── The Wall: a foreign tenant cannot reach this PO ──────────────────────────


async def test_foreign_tenant_cannot_load_po(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    po_id = await _draft(db_session, seed)
    other_tenant = uuid4()

    with pytest.raises(PurchaseOrderNotFound):
        await svc.approve(tenant_id=other_tenant, po_id=po_id, approver_id=seed.user_id)


# ── has_open_po_for_product: idempotent-drafting guard (Task 4.9) ────────────


async def test_has_open_po_is_true_for_draft_and_approved(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    repo = PurchaseOrderRepository(db_session)

    assert await repo.has_open_po_for_product(seed.tenant_id, seed.product_id) is False

    po_id = await _draft(db_session, seed)
    assert await repo.has_open_po_for_product(seed.tenant_id, seed.product_id) is True

    await svc.approve(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id)
    # approved-unsent is still "open" — no second draft should be allowed.
    assert await repo.has_open_po_for_product(seed.tenant_id, seed.product_id) is True


async def test_has_open_po_is_false_after_reject_or_sent(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    repo = PurchaseOrderRepository(db_session)

    # Rejected → not open (a new draft is allowed next time stock drops).
    po_id = await _draft(db_session, seed)
    await svc.reject(tenant_id=seed.tenant_id, po_id=po_id, approver_id=seed.user_id, reason="no")
    assert await repo.has_open_po_for_product(seed.tenant_id, seed.product_id) is False

    # Sent → not open.
    po2 = await _draft(db_session, seed)
    await svc.approve(tenant_id=seed.tenant_id, po_id=po2, approver_id=seed.user_id)
    await svc.mark_dispatched(tenant_id=seed.tenant_id, po_id=po2)
    assert await repo.has_open_po_for_product(seed.tenant_id, seed.product_id) is False


async def test_has_open_po_is_scoped_to_tenant(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    await _draft(db_session, seed)
    other_tenant = uuid4()
    # The same product_id under a foreign scope sees nothing.
    repo = PurchaseOrderRepository(db_session)
    assert await repo.has_open_po_for_product(other_tenant, seed.product_id) is False


# ── Inbox listing: tenant-scoped, drafts + manual queue ──────────────────────


async def test_inbox_lists_drafts_and_failed_only(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = PurchaseOrderService(db_session)
    repo = PurchaseOrderRepository(db_session)

    draft_id = await _draft(db_session, seed)  # stays draft
    # A second PO driven to dispatch_failed (manual queue).
    failed_id = await _draft(db_session, seed)
    await svc.approve(tenant_id=seed.tenant_id, po_id=failed_id, approver_id=seed.user_id)
    await svc.mark_dispatch_failed(tenant_id=seed.tenant_id, po_id=failed_id, error="boom")
    # A third PO sent — should NOT appear in the inbox.
    sent_id = await _draft(db_session, seed)
    await svc.approve(tenant_id=seed.tenant_id, po_id=sent_id, approver_id=seed.user_id)
    await svc.mark_dispatched(tenant_id=seed.tenant_id, po_id=sent_id)

    rows = await repo.list_for_inbox(seed.tenant_id, limit=50, offset=0)
    ids = {po.id for po, _product, _supplier in rows}
    assert draft_id in ids
    assert failed_id in ids
    assert sent_id not in ids
    assert await repo.count_for_inbox(seed.tenant_id) == 2


async def test_inbox_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _seed(db_session)
    b = await _seed(db_session)
    await _draft(db_session, a)
    b_draft = await _draft(db_session, b)

    repo = PurchaseOrderRepository(db_session)
    a_rows = await repo.list_for_inbox(a.tenant_id, limit=50, offset=0)
    a_ids = {po.id for po, _p, _s in a_rows}
    assert b_draft not in a_ids
    assert await repo.count_for_inbox(a.tenant_id) == 1
