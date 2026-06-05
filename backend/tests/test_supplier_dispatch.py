"""Task 4.11 — SupplierDispatcher: the gated, dev-safe webhook send leg.

These tests prove the dispatch leg of the HIL loop:

- THE GATE COMES FIRST: an absent / forged / mismatched / wrong-tenant token is
  refused (UnauthorizedAction) and NOTHING is sent — no status flip, no payload.
  This is the constitution-V "accidental call without authorization is rejected"
  guard at the boundary that actually side-effects.
- Happy path: a valid token + a reachable (stubbed) channel → PO `sent`,
  `dispatched_at` set, attempts counted.
- Retry → manual: an always-failing send exhausts the retry budget → the PO is
  `dispatch_failed` (the manual queue) with the error recorded.

No real network: the send is stubbed (dev mode logs; webhook mode is monkeypatched
or pointed at a transport mock). The DB is real (constitution: real Postgres), but
the dispatcher's own session is fed the test's transactional session so writes are
visible in-test and rolled back at the end.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Product,
    PurchaseOrder,
    Supplier,
    Tenant,
    User,
)
from app.infra.action_gate import UnauthorizedAction, mint_approval_token
from app.infra.security import hash_password
from app.infra.settings import Settings
from app.infra.supplier_dispatch import DISPATCH_ACTION, SupplierDispatcher
from app.services.purchase_orders import PurchaseOrderService


def _settings(**overrides) -> Settings:
    """A minimal Settings for crypto + dispatch, built like test_action_gate.py.

    Defaults to dev mode with a tight, fast retry budget so the retry-exhaustion
    test does not actually sleep for seconds.
    """
    base = dict(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=30,
        po_dispatch_mode="dev",
        po_dispatch_max_retries=2,
        po_dispatch_backoff_seconds=0.0,  # no real waiting in tests
        po_dispatch_webhook_secret=SecretStr(""),
    )
    base.update(overrides)
    return Settings.model_construct(**base)


def _sessionmaker_for(session: AsyncSession):
    """A fake async_sessionmaker that always hands back the test's transactional
    session. close()/commit() must NOT tear down the outer transaction, so we
    wrap it to no-op those — the conftest fixture owns the real lifecycle."""

    class _NoCloseSession:
        def __init__(self, inner: AsyncSession) -> None:
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def commit(self) -> None:
            # Flush so the writes are visible to later in-test queries, but never
            # commit the outer transaction the fixture will roll back.
            await self._inner.flush()

        async def close(self) -> None:
            return None

    @asynccontextmanager
    async def _maker():
        yield _NoCloseSession(session)

    return _maker


@dataclass
class _Seed:
    tenant_id: UUID
    user_id: UUID
    product_id: UUID
    supplier_id: UUID


async def _seed(db: AsyncSession, *, webhook_url: str | None = "https://supplier/hook") -> _Seed:
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
    supplier = Supplier(tenant_id=tenant.id, name="مورّد", webhook_url=webhook_url)
    db.add_all([user, product, supplier])
    await db.flush()
    return _Seed(
        tenant_id=tenant.id,
        user_id=user.id,
        product_id=product.id,
        supplier_id=supplier.id,
    )


async def _approved_po(db: AsyncSession, seed: _Seed) -> PurchaseOrder:
    """Walk a PO to `approved` (the only status the dispatcher acts on)."""
    svc = PurchaseOrderService(db)
    po = await svc.draft(
        tenant_id=seed.tenant_id,
        product_id=seed.product_id,
        supplier_id=seed.supplier_id,
        quantity=10,
        reason="crossed reorder threshold",
        agent_note_ar="بدنا نطلب كمان",
    )
    await svc.approve(tenant_id=seed.tenant_id, po_id=po.id, approver_id=seed.user_id)
    return po


async def _supplier(db: AsyncSession, seed: _Seed) -> Supplier:
    return (await db.execute(select(Supplier).where(Supplier.id == seed.supplier_id))).scalar_one()


async def _status(db: AsyncSession, po_id: UUID) -> str:
    return (
        await db.execute(select(PurchaseOrder.status).where(PurchaseOrder.id == po_id))
    ).scalar_one()


def _token(settings: Settings, po: PurchaseOrder, approver_id: UUID, **overrides) -> str:
    kwargs = dict(
        action=DISPATCH_ACTION,
        resource_id=po.id,
        tenant_id=po.tenant_id,
        approver_id=approver_id,
    )
    kwargs.update(overrides)
    return mint_approval_token(settings, **kwargs)


# ── THE GATE COMES FIRST: no token, no send ──────────────────────────────────


async def test_absent_token_refuses_and_never_sends(db_session: AsyncSession) -> None:
    """No token → UnauthorizedAction, the PO stays `approved`, nothing dispatched.
    The constitution-V 'accidental call without authorization is rejected' test."""
    settings = _settings()
    seed = await _seed(db_session)
    po = await _approved_po(db_session, seed)
    supplier = await _supplier(db_session, seed)

    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))

    sent_calls: list = []
    dispatcher._send = lambda *a, **k: sent_calls.append(a)  # type: ignore[assignment]

    with pytest.raises(UnauthorizedAction):
        await dispatcher.dispatch(po, supplier, None)

    assert sent_calls == []  # never even attempted a send
    assert await _status(db_session, po.id) == "approved"  # no status flip


async def test_token_for_a_different_po_refuses(db_session: AsyncSession) -> None:
    """Replaying another PO's token to dispatch THIS one → refused, nothing sent."""
    settings = _settings()
    seed = await _seed(db_session)
    po = await _approved_po(db_session, seed)
    supplier = await _supplier(db_session, seed)

    foreign_token = _token(settings, po, seed.user_id, resource_id=uuid4())

    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))
    sent_calls: list = []
    dispatcher._send = lambda *a, **k: sent_calls.append(a)  # type: ignore[assignment]

    with pytest.raises(UnauthorizedAction):
        await dispatcher.dispatch(po, supplier, foreign_token)
    assert sent_calls == []
    assert await _status(db_session, po.id) == "approved"


async def test_token_for_a_different_tenant_refuses(db_session: AsyncSession) -> None:
    """The Wall: a token minted under another tenant cannot send this tenant's PO."""
    settings = _settings()
    seed = await _seed(db_session)
    po = await _approved_po(db_session, seed)
    supplier = await _supplier(db_session, seed)

    foreign_token = _token(settings, po, seed.user_id, tenant_id=uuid4())

    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))
    sent_calls: list = []
    dispatcher._send = lambda *a, **k: sent_calls.append(a)  # type: ignore[assignment]

    with pytest.raises(UnauthorizedAction):
        await dispatcher.dispatch(po, supplier, foreign_token)
    assert sent_calls == []


# ── Happy path: a valid token sends and marks the PO sent ────────────────────


async def test_valid_token_dev_mode_marks_sent(db_session: AsyncSession) -> None:
    """A valid token in dev mode → PO `sent`, dispatched_at set, one attempt."""
    settings = _settings(po_dispatch_mode="dev")
    seed = await _seed(db_session)
    po = await _approved_po(db_session, seed)
    supplier = await _supplier(db_session, seed)
    token = _token(settings, po, seed.user_id)

    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))
    await dispatcher.dispatch(po, supplier, token)

    assert await _status(db_session, po.id) == "sent"
    refreshed = (
        await db_session.execute(select(PurchaseOrder).where(PurchaseOrder.id == po.id))
    ).scalar_one()
    assert refreshed.dispatched_at is not None
    assert refreshed.dispatch_attempts == 1


async def test_valid_token_webhook_mode_posts_then_marks_sent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Webhook mode: a 2xx from the (mocked) transport → PO `sent`. Proves the
    payload is POSTed via httpx (never requests) with the supplier's webhook_url."""
    settings = _settings(
        po_dispatch_mode="webhook",
        po_dispatch_webhook_secret=SecretStr("shared-hmac-secret"),
    )
    seed = await _seed(db_session)
    po = await _approved_po(db_session, seed)
    supplier = await _supplier(db_session, seed)
    token = _token(settings, po, seed.user_id)

    captured: dict = {}

    # **kwargs absorbs the timeout= the dispatcher passes (naming a `timeout`
    # param trips ruff ASYNC109; this stub is not the thing that should own one).
    async def _fake_post(self, url, *, json, headers, **kwargs):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))
    await dispatcher.dispatch(po, supplier, token)

    assert captured["url"] == supplier.webhook_url
    assert captured["json"]["purchase_order_id"] == str(po.id)
    assert captured["json"]["quantity"] == po.quantity
    # The shared secret rides as the auth header so the supplier can verify origin.
    assert captured["headers"]["X-Modir-Signature"] == "shared-hmac-secret"
    assert await _status(db_session, po.id) == "sent"


# ── Retry → manual queue: an always-failing send exhausts the budget ─────────


async def test_failing_send_exhausts_retries_then_dispatch_failed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A send that always raises → retries exhaust → PO `dispatch_failed` with the
    error recorded (the manual queue). Backoff is 0s in tests so this is fast."""
    settings = _settings(po_dispatch_mode="webhook", po_dispatch_max_retries=2)
    seed = await _seed(db_session)
    po = await _approved_po(db_session, seed)
    supplier = await _supplier(db_session, seed)
    token = _token(settings, po, seed.user_id)

    attempts = {"n": 0}

    async def _always_fail(self, url, *, json, headers, **kwargs):  # noqa: A002
        attempts["n"] += 1
        raise httpx.ConnectError("supplier unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "post", _always_fail)

    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))
    # Delivery failure is absorbed (a down supplier never crashes the task).
    await dispatcher.dispatch(po, supplier, token)

    assert attempts["n"] == settings.po_dispatch_max_retries + 1  # 1 try + 2 retries
    refreshed = (
        await db_session.execute(select(PurchaseOrder).where(PurchaseOrder.id == po.id))
    ).scalar_one()
    assert refreshed.status == "dispatch_failed"
    assert refreshed.dispatch_error is not None
    assert "supplier unreachable" in refreshed.dispatch_error


async def test_webhook_with_no_url_lands_in_manual_queue(db_session: AsyncSession) -> None:
    """A supplier configured for webhook mode but missing a URL → not retryable
    noise that hangs; it surfaces as dispatch_failed for the owner to handle."""
    settings = _settings(po_dispatch_mode="webhook")
    seed = await _seed(db_session, webhook_url=None)
    po = await _approved_po(db_session, seed)
    supplier = await _supplier(db_session, seed)
    token = _token(settings, po, seed.user_id)

    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))
    await dispatcher.dispatch(po, supplier, token)

    assert await _status(db_session, po.id) == "dispatch_failed"
