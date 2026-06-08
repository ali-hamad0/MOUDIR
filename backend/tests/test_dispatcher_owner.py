"""Task 7.7 — owner dispatch wiring tests.

Verifies that MessageDispatcher routes owner messages through OwnerSupervisor
when one is wired, and falls back to OWNER_PLACEHOLDER when supervisor=None
(backward-compatibility guarantee so pre-Phase 7 tests stay green).

Tests run entirely in memory — no DB, no LLM.
"""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from app.domain.identity import ResolvedIdentity
from app.infra.session import make_session_id
from app.services.dispatcher import MessageDispatcher
from prompts import order_ar


class _FakeTenant:
    def __init__(self) -> None:
        self.id = uuid4()


class _FakeActor:
    def __init__(self) -> None:
        self.id = uuid4()


def _owner_identity() -> ResolvedIdentity:
    return ResolvedIdentity(tenant=_FakeTenant(), role="owner", actor=_FakeActor())


def _noop_sessionmaker():
    @asynccontextmanager
    async def _cm():
        raise AssertionError("session must not be opened on the owner path")
        yield  # pragma: no cover

    return lambda: _cm()


class _SpySupervisor:
    """Records the arguments passed to handle() and returns a fixed reply."""

    def __init__(self, reply: str = "SUPERVISOR_REPLY") -> None:
        self.calls: list[tuple[str, UUID, str]] = []
        self._reply = reply

    async def handle(self, message: str, tenant_id: UUID, session_id: str) -> str:
        self.calls.append((message, tenant_id, session_id))
        return self._reply


async def test_owner_routes_to_supervisor_when_wired():
    identity = _owner_identity()
    spy = _SpySupervisor()
    reply = await MessageDispatcher(object(), _noop_sessionmaker(), supervisor=spy).dispatch(
        "كيف المبيعات اليوم؟", identity
    )
    assert reply == "SUPERVISOR_REPLY"
    assert len(spy.calls) == 1


async def test_supervisor_receives_correct_tenant_id():
    identity = _owner_identity()
    spy = _SpySupervisor()
    await MessageDispatcher(object(), _noop_sessionmaker(), supervisor=spy).dispatch(
        "كيف المبيعات اليوم؟", identity
    )
    _, tenant_id, _ = spy.calls[0]
    assert tenant_id == identity.tenant.id


async def test_supervisor_receives_session_id_from_make_session_id():
    identity = _owner_identity()
    spy = _SpySupervisor()
    await MessageDispatcher(object(), _noop_sessionmaker(), supervisor=spy).dispatch(
        "كيف المبيعات اليوم؟", identity
    )
    _, _, session_id = spy.calls[0]
    assert session_id == make_session_id(identity)
    assert session_id == str(identity.actor.id)


async def test_supervisor_receives_message_text():
    identity = _owner_identity()
    spy = _SpySupervisor()
    await MessageDispatcher(object(), _noop_sessionmaker(), supervisor=spy).dispatch(
        "شو المنتجات الأكثر مبيعاً؟", identity
    )
    message, _, _ = spy.calls[0]
    assert message == "شو المنتجات الأكثر مبيعاً؟"


async def test_none_text_becomes_empty_string_for_supervisor():
    identity = _owner_identity()
    spy = _SpySupervisor()
    await MessageDispatcher(object(), _noop_sessionmaker(), supervisor=spy).dispatch(None, identity)
    message, _, _ = spy.calls[0]
    assert message == ""


async def test_supervisor_not_none_so_order_agent_never_called():
    """The OrderAgent object is never touched when the owner path uses the supervisor."""
    identity = _owner_identity()
    spy = _SpySupervisor()

    class _ForbiddenAgent:
        async def handle(self, *args, **kwargs):
            raise AssertionError("OrderAgent must not be called on the owner path")

    await MessageDispatcher(_ForbiddenAgent(), _noop_sessionmaker(), supervisor=spy).dispatch(
        "كم طلب وصلني اليوم؟", identity
    )
    assert len(spy.calls) == 1


async def test_fallback_to_placeholder_when_supervisor_is_none():
    identity = _owner_identity()
    reply = await MessageDispatcher(object(), _noop_sessionmaker(), supervisor=None).dispatch(
        "مرحبا", identity
    )
    assert reply == order_ar.OWNER_PLACEHOLDER


async def test_different_owners_get_different_session_ids():
    id1 = _owner_identity()
    id2 = _owner_identity()
    assert make_session_id(id1) != make_session_id(id2)


async def test_same_owner_always_gets_same_session_id():
    identity = _owner_identity()
    assert make_session_id(identity) == make_session_id(identity)


async def test_supervisor_called_once_per_dispatch():
    """Dispatcher calls supervisor.handle() exactly once per owner message."""
    identity = _owner_identity()
    spy = _SpySupervisor()
    dispatcher = MessageDispatcher(object(), _noop_sessionmaker(), supervisor=spy)
    await dispatcher.dispatch("رسالة ١", identity)
    await dispatcher.dispatch("رسالة ٢", identity)
    assert len(spy.calls) == 2


async def test_tenant_isolation_different_tenants_different_ids():
    """Two different owners get different tenant_ids passed to the supervisor."""
    id1 = _owner_identity()
    id2 = _owner_identity()
    spy = _SpySupervisor()
    dispatcher = MessageDispatcher(object(), _noop_sessionmaker(), supervisor=spy)
    await dispatcher.dispatch("سؤال", id1)
    await dispatcher.dispatch("سؤال", id2)
    _, t1, _ = spy.calls[0]
    _, t2, _ = spy.calls[1]
    assert t1 != t2
    assert t1 == id1.tenant.id
    assert t2 == id2.tenant.id
