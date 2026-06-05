"""Task 4.13 — the consolidated Human-in-the-Loop proof for purchase orders.

This is the end-to-end evidence that constitution V holds for the reorder loop.
It exercises the REAL pieces together — the InventoryAgent draft, the
PurchaseOrderService lifecycle, the ActionGate token, and the SupplierDispatcher
send — mocking only the LLM (the agent's Arabic note) and stubbing the webhook
(no real network). The suite stays offline like the rest.

What it proves (the spec's seven points):
  1. THE GATE HOLDS — dispatch with no / forged / wrong-PO token is refused; the
     PO is never `sent` (the "accidental call without authorization is rejected"
     reading of constitution V).
  2. HAPPY PATH — draft → approve → (token minted) → dispatch → `sent`,
     dispatched_at set, webhook stubbed.
  3. REJECT — reject → `rejected` + reason; never dispatched; provisions nothing.
  4. RETRY → MANUAL — an always-failing webhook exhausts retries → `dispatch_failed`,
     shows in the inbox query; `mark_sent_manually` closes it.
  5. NO AUTO-SEND — an agent-drafted PO is `draft` and was never dispatched; the
     send is not reachable from drafting.
  6. THE WALL — tenant A's owner cannot load/approve tenant B's PO (scoped → 404);
     the dispatch is bound to the PO's own tenant.
  7. AUDIT — drafted / approved / rejected / sent / dispatch_failed / sent_manually
     each write an audit row.

Negative control the spec asks for (run by hand, see the module-end note): make
the dispatcher SKIP ActionGate.authorize → assertion (1) FAILS. That is the proof
the gate is the thing doing the work, not the status flag.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.inventory.agent import InventoryAgent
from app.agents.inventory.schemas import SupplierNote
from app.db.models import (
    AuditLog,
    Inventory,
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
from app.repositories.purchase_orders import INBOX_STATUSES, PurchaseOrderRepository
from app.services.purchase_orders import PurchaseOrderService

# ── Offline harness (mirrors test_supplier_dispatch.py + test_inventory_agent.py) ──


def _settings(**overrides) -> Settings:
    """Crypto + dispatch settings, built like test_action_gate.py. Dev mode with a
    tiny, zero-backoff retry budget so failure tests are fast and deterministic."""
    base = dict(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=30,
        po_dispatch_mode="dev",
        po_dispatch_max_retries=2,
        po_dispatch_backoff_seconds=0.0,
        po_dispatch_webhook_secret=SecretStr(""),
    )
    base.update(overrides)
    return Settings.model_construct(**base)


def _sessionmaker_for(session: AsyncSession):
    """Hand the dispatcher/agent the test's transactional session. commit() flushes
    (so writes are visible in-test) but never tears down the outer transaction the
    conftest fixture rolls back; close() is a no-op for the same reason."""

    class _NoCloseSession:
        def __init__(self, inner: AsyncSession) -> None:
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def commit(self) -> None:
            await self._inner.flush()

        async def close(self) -> None:
            return None

    @asynccontextmanager
    async def _maker():
        yield _NoCloseSession(session)

    return _maker


# ---- LLM fakes for the agent draft path (same shape as the agent tests) ----
class _FakeStructured:
    def __init__(self, script: list) -> None:
        self._script = list(script)

    async def ainvoke(self, messages):
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


@dataclass
class _Seed:
    tenant_id: UUID
    user_id: UUID
    product_id: UUID
    supplier_id: UUID


async def _seed(
    db: AsyncSession, *, name: str = "ShopA", quantity: int = 2, threshold: int = 5
) -> _Seed:
    """A tenant with an owner-user, a tracked low-stock product, and a supplier."""
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
    db.add(
        Inventory(
            tenant_id=tenant.id,
            product_id=product.id,
            quantity=quantity,
            reorder_threshold=threshold,
            reorder_quantity=12,
            supplier_id=supplier.id,
        )
    )
    await db.flush()
    return _Seed(
        tenant_id=tenant.id, user_id=user.id, product_id=product.id, supplier_id=supplier.id
    )


def _agent(db: AsyncSession, note_ar: str = "بدنا نطلب كمان") -> InventoryAgent:
    router = _FakeRouter(_FakeStructured([SupplierNote(note_ar=note_ar)]))
    return InventoryAgent(router, _settings(), _sessionmaker_for(db))


async def _draft_via_agent(db: AsyncSession, seed: _Seed) -> PurchaseOrder:
    """Drive the REAL agent to draft a reorder PO (the way completion does)."""
    po_id = await _agent(db).draft_for_low_stock(seed.tenant_id, seed.product_id)
    assert po_id is not None
    return await _load(db, po_id)


async def _approve(db: AsyncSession, seed: _Seed, po: PurchaseOrder) -> str:
    """Approve a draft PO (service layer) and mint the dispatch token the API would
    mint on commit. Returns the token."""
    await PurchaseOrderService(db).approve(
        tenant_id=seed.tenant_id, po_id=po.id, approver_id=seed.user_id
    )
    return mint_approval_token(
        _settings(),
        action=DISPATCH_ACTION,
        resource_id=po.id,
        tenant_id=seed.tenant_id,
        approver_id=seed.user_id,
    )


async def _supplier(db: AsyncSession, seed: _Seed) -> Supplier:
    return (await db.execute(select(Supplier).where(Supplier.id == seed.supplier_id))).scalar_one()


async def _load(db: AsyncSession, po_id: UUID) -> PurchaseOrder:
    return (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()


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


def _stub_webhook_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(self, url, *, json, headers, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _ok)


def _stub_webhook_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(self, url, *, json, headers, **kwargs):
        raise httpx.ConnectError("supplier unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "post", _fail)


# ── 1. THE GATE HOLDS: no send without a valid signed token ──────────────────


async def test_dispatch_with_no_token_is_refused_and_never_sends(
    db_session: AsyncSession,
) -> None:
    seed = await _seed(db_session)
    po = await _draft_via_agent(db_session, seed)
    await _approve(db_session, seed, po)  # status approved, but NO token presented
    supplier = await _supplier(db_session, seed)

    dispatcher = SupplierDispatcher(_settings(), _sessionmaker_for(db_session))
    sent: list = []
    dispatcher._send = lambda *a, **k: sent.append(a)  # type: ignore[assignment]

    with pytest.raises(UnauthorizedAction):
        await dispatcher.dispatch(po, supplier, None)
    assert sent == []
    assert await _status(db_session, po.id) == "approved"  # never sent


async def test_dispatch_with_forged_token_is_refused(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    po = await _draft_via_agent(db_session, seed)
    await _approve(db_session, seed, po)
    supplier = await _supplier(db_session, seed)

    # A token signed with a DIFFERENT secret (an attacker who doesn't hold Vault's
    # jwt_secret) — structurally a JWT, but the signature won't verify.
    forged = mint_approval_token(
        _settings(jwt_secret=SecretStr("a-totally-different-attacker-secret")),
        action=DISPATCH_ACTION,
        resource_id=po.id,
        tenant_id=seed.tenant_id,
        approver_id=seed.user_id,
    )
    dispatcher = SupplierDispatcher(_settings(), _sessionmaker_for(db_session))
    with pytest.raises(UnauthorizedAction):
        await dispatcher.dispatch(po, supplier, forged)
    assert await _status(db_session, po.id) == "approved"


async def test_dispatch_with_another_pos_token_is_refused(db_session: AsyncSession) -> None:
    """Replaying a token minted for a DIFFERENT PO → refused (resource mismatch)."""
    seed = await _seed(db_session)
    po = await _draft_via_agent(db_session, seed)
    await _approve(db_session, seed, po)
    supplier = await _supplier(db_session, seed)

    other_pos_token = mint_approval_token(
        _settings(),
        action=DISPATCH_ACTION,
        resource_id=uuid4(),  # some other PO
        tenant_id=seed.tenant_id,
        approver_id=seed.user_id,
    )
    dispatcher = SupplierDispatcher(_settings(), _sessionmaker_for(db_session))
    with pytest.raises(UnauthorizedAction):
        await dispatcher.dispatch(po, supplier, other_pos_token)
    assert await _status(db_session, po.id) == "approved"


# ── 2. HAPPY PATH: draft → approve → token → dispatch → sent ─────────────────


async def test_happy_path_draft_approve_dispatch_sent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_webhook_ok(monkeypatch)
    seed = await _seed(db_session)
    po = await _draft_via_agent(db_session, seed)
    assert po.status == "draft"  # the agent drafted, did not send

    token = await _approve(db_session, seed, po)
    supplier = await _supplier(db_session, seed)

    dispatcher = SupplierDispatcher(
        _settings(po_dispatch_mode="webhook"), _sessionmaker_for(db_session)
    )
    await dispatcher.dispatch(po, supplier, token)

    refreshed = await _load(db_session, po.id)
    assert refreshed.status == "sent"
    assert refreshed.dispatched_at is not None
    assert await _audit_count(db_session, "po.sent", po.id) == 1


# ── 3. REJECT: provisions nothing, never dispatched ──────────────────────────


async def test_reject_path_provisions_nothing(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    po = await _draft_via_agent(db_session, seed)

    rejected = await PurchaseOrderService(db_session).reject(
        tenant_id=seed.tenant_id, po_id=po.id, approver_id=seed.user_id, reason="مش وقتها"
    )
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "مش وقتها"
    assert rejected.dispatched_at is None
    assert await _audit_count(db_session, "po.rejected", po.id) == 1
    # A rejected PO is not "open" — it can never be approved/dispatched from here.
    repo = PurchaseOrderRepository(db_session)
    assert await repo.has_open_po_for_product(seed.tenant_id, seed.product_id) is False


# ── 4. RETRY → MANUAL: failing webhook exhausts retries → dispatch_failed ─────


async def test_retry_exhaustion_lands_in_manual_queue_then_mark_sent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_webhook_fail(monkeypatch)
    seed = await _seed(db_session)
    po = await _draft_via_agent(db_session, seed)
    token = await _approve(db_session, seed, po)
    supplier = await _supplier(db_session, seed)

    settings = _settings(po_dispatch_mode="webhook", po_dispatch_max_retries=2)
    dispatcher = SupplierDispatcher(settings, _sessionmaker_for(db_session))
    # The failure is absorbed (a down supplier never crashes the background task).
    await dispatcher.dispatch(po, supplier, token)

    failed = await _load(db_session, po.id)
    assert failed.status == "dispatch_failed"
    assert failed.dispatch_error is not None
    assert await _audit_count(db_session, "po.dispatch_failed", po.id) == 1

    # It shows up in the inbox manual queue...
    repo = PurchaseOrderRepository(db_session)
    rows = await repo.list_for_inbox(seed.tenant_id, statuses=INBOX_STATUSES, limit=50, offset=0)
    assert po.id in {r_po.id for r_po, _p, _s in rows}

    # ...and the owner can close it out of band.
    closed = await PurchaseOrderService(db_session).mark_sent_manually(
        tenant_id=seed.tenant_id, po_id=po.id, actor_id=seed.user_id
    )
    assert closed.status == "sent"
    assert await _audit_count(db_session, "po.sent_manually", po.id) == 1


# ── 5. NO AUTO-SEND: the agent draft is never dispatched on its own ──────────


async def test_agent_draft_is_never_auto_sent(db_session: AsyncSession) -> None:
    """The whole point of the gate: drafting does not (and cannot) send. The agent
    produces a `draft` with no dispatched_at, and dispatch is a SEPARATE call that
    requires a token the drafting path never mints."""
    seed = await _seed(db_session)
    po = await _draft_via_agent(db_session, seed)

    assert po.status == "draft"
    assert po.dispatched_at is None
    assert po.dispatch_attempts == 0
    # No sent/dispatch_failed audit exists — nothing was dispatched.
    assert await _audit_count(db_session, "po.sent", po.id) == 0
    assert await _audit_count(db_session, "po.dispatch_failed", po.id) == 0
    assert await _audit_count(db_session, "po.drafted", po.id) == 1


# ── 6. THE WALL: a foreign tenant cannot approve / dispatch another's PO ──────


async def test_foreign_tenant_cannot_approve_anothers_po(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    a_po = await _draft_via_agent(db_session, a)

    from app.services.purchase_orders import PurchaseOrderNotFound

    # B's owner approving A's PO → scoped load misses → 404-shaped not-found.
    with pytest.raises(PurchaseOrderNotFound):
        await PurchaseOrderService(db_session).approve(
            tenant_id=b.tenant_id, po_id=a_po.id, approver_id=b.user_id
        )
    assert await _status(db_session, a_po.id) == "draft"  # untouched


async def test_token_minted_under_one_tenant_cannot_dispatch_anothers(
    db_session: AsyncSession,
) -> None:
    """Even a validly-signed token is bound to ONE tenant (the Wall in the gate):
    A's token cannot authorize a dispatch evaluated under B's tenant."""
    a = await _seed(db_session, name="ShopA")
    a_po = await _draft_via_agent(db_session, a)
    await _approve(db_session, a, a_po)
    a_supplier = await _supplier(db_session, a)

    # A token minted for a DIFFERENT tenant than the PO actually belongs to.
    cross_tenant_token = mint_approval_token(
        _settings(),
        action=DISPATCH_ACTION,
        resource_id=a_po.id,
        tenant_id=uuid4(),  # not a_po.tenant_id
        approver_id=a.user_id,
    )
    dispatcher = SupplierDispatcher(_settings(), _sessionmaker_for(db_session))
    with pytest.raises(UnauthorizedAction):
        await dispatcher.dispatch(a_po, a_supplier, cross_tenant_token)
    assert await _status(db_session, a_po.id) == "approved"


# ── 7. AUDIT: every PO action writes a row ───────────────────────────────────


async def test_happy_line_audit_trail(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drafted → approved → sent each leave an audit row. The other branches —
    `po.rejected` (test #3), `po.dispatch_failed` + `po.sent_manually` (test #4) —
    are asserted in their own tests, so the seven action audits are covered across
    the suite."""
    _stub_webhook_ok(monkeypatch)
    seed = await _seed(db_session)

    # Happy line: drafted + approved + sent.
    po = await _draft_via_agent(db_session, seed)
    token = await _approve(db_session, seed, po)
    supplier = await _supplier(db_session, seed)
    await SupplierDispatcher(
        _settings(po_dispatch_mode="webhook"), _sessionmaker_for(db_session)
    ).dispatch(po, supplier, token)

    assert await _audit_count(db_session, "po.drafted", po.id) == 1
    assert await _audit_count(db_session, "po.approved", po.id) == 1
    assert await _audit_count(db_session, "po.sent", po.id) == 1
