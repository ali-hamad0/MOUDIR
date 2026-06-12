"""Phase 10 — saved owner chat sessions (history per conversation).

Proves the service layer end to end against real Postgres: sessions are created
per (tenant, user), messages persist in order with the agent badge, the title
comes from the first message, and the Wall holds — another tenant's (or another
user's) session id is a 404, never a read.

The supervisor is a stub: routing correctness is proven in
test_supervisor_routing.py / test_inventory_whatsapp_adjust.py; here we prove
persistence and scoping.
"""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.infra.security import hash_password
from app.repositories.users import UserRepository
from app.services.chat import ChatService
from tests.conftest import TwoTenants


class _StubSupervisor:
    def __init__(self, reply: str = "رد مدير") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []  # (message, session_id)

    async def handle(self, message, tenant_id, session_id):
        self.calls.append((message, session_id))
        return self.reply


class _StubCheckpointer:
    """Returns a checkpoint whose routed_to names the agent badge."""

    def __init__(self, routed_to: str = "inventory") -> None:
        self._routed_to = routed_to

    async def aget_tuple(self, config):
        from types import SimpleNamespace

        return SimpleNamespace(checkpoint={"channel_values": {"routed_to": self._routed_to}})


async def _user(db: AsyncSession, tenant_id, email) -> User:
    user = await UserRepository(db).get_by_email(tenant_id, email)
    assert user is not None
    return user


async def test_session_lifecycle_and_history(db_session: AsyncSession, two_tenants: TwoTenants):
    a = two_tenants.a
    user = await _user(db_session, a.tenant_id, a.user_email)
    svc = ChatService(db_session)

    chat = await svc.create_session(tenant_id=a.tenant_id, user_id=user.id)
    assert chat.title is None

    supervisor = _StubSupervisor("مخزونك تمام!")
    reply, agent = await svc.send_message(
        tenant_id=a.tenant_id,
        user_id=user.id,
        session_id=chat.id,
        message="شو ناقص من المخزون؟",
        supervisor=supervisor,
        checkpointer=_StubCheckpointer("inventory"),
    )

    assert reply == "مخزونك تمام!"
    assert agent == "inventory"
    # The supervisor ran on THIS session's thread.
    assert supervisor.calls == [("شو ناقص من المخزون؟", str(chat.id))]

    messages = await svc.list_messages(tenant_id=a.tenant_id, user_id=user.id, session_id=chat.id)
    assert [(m.role, m.content, m.agent) for m in messages] == [
        ("owner", "شو ناقص من المخزون؟", None),
        ("modir", "مخزونك تمام!", "inventory"),
    ]
    # Title = first owner message; it does not change on later messages.
    assert chat.title == "شو ناقص من المخزون؟"
    await svc.send_message(
        tenant_id=a.tenant_id,
        user_id=user.id,
        session_id=chat.id,
        message="وشو كمان؟",
        supervisor=supervisor,
        checkpointer=_StubCheckpointer("advisor"),
    )
    assert chat.title == "شو ناقص من المخزون؟"
    messages = await svc.list_messages(tenant_id=a.tenant_id, user_id=user.id, session_id=chat.id)
    assert len(messages) == 4


async def test_sessions_list_most_recent_first(db_session: AsyncSession, two_tenants: TwoTenants):
    a = two_tenants.a
    user = await _user(db_session, a.tenant_id, a.user_email)
    svc = ChatService(db_session)

    first = await svc.create_session(tenant_id=a.tenant_id, user_id=user.id)
    second = await svc.create_session(tenant_id=a.tenant_id, user_id=user.id)
    # Activity on the FIRST session moves it back to the top.
    await svc.send_message(
        tenant_id=a.tenant_id,
        user_id=user.id,
        session_id=first.id,
        message="مرحبا",
        supervisor=_StubSupervisor(),
        checkpointer=_StubCheckpointer(),
    )

    rows = await svc.list_sessions(tenant_id=a.tenant_id, user_id=user.id)
    assert [r.id for r in rows][:2] == [first.id, second.id]


async def test_cross_tenant_session_is_404(db_session: AsyncSession, two_tenants: TwoTenants):
    a, b = two_tenants.a, two_tenants.b
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    user_b = await _user(db_session, b.tenant_id, b.user_email)
    svc = ChatService(db_session)

    chat_b = await svc.create_session(tenant_id=b.tenant_id, user_id=user_b.id)

    with pytest.raises(HTTPException) as exc:
        await svc.list_messages(tenant_id=a.tenant_id, user_id=user_a.id, session_id=chat_b.id)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await svc.send_message(
            tenant_id=a.tenant_id,
            user_id=user_a.id,
            session_id=chat_b.id,
            message="اعطيني محادثة التاني",
            supervisor=_StubSupervisor(),
            checkpointer=_StubCheckpointer(),
        )
    assert exc.value.status_code == 404

    # B's listing never shows A anything; A's listing is empty.
    assert await svc.list_sessions(tenant_id=a.tenant_id, user_id=user_a.id) == []


async def test_another_users_session_is_404(db_session: AsyncSession, two_tenants: TwoTenants):
    """Per-user privacy INSIDE a tenant: a second dashboard user of the same
    shop cannot read a colleague's conversation."""
    a = two_tenants.a
    user1 = await _user(db_session, a.tenant_id, a.user_email)
    user2 = User(
        tenant_id=a.tenant_id,
        email=f"second-{uuid4().hex[:6]}@a.com",
        hashed_password=hash_password("password123"),
        role="owner",
    )
    db_session.add(user2)
    await db_session.flush()

    svc = ChatService(db_session)
    chat1 = await svc.create_session(tenant_id=a.tenant_id, user_id=user1.id)

    with pytest.raises(HTTPException) as exc:
        await svc.list_messages(tenant_id=a.tenant_id, user_id=user2.id, session_id=chat1.id)
    assert exc.value.status_code == 404
    assert await svc.list_sessions(tenant_id=a.tenant_id, user_id=user2.id) == []
