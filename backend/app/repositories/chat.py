"""Tenant-scoped repositories for the owner chat history (Phase 10).

Sessions are additionally scoped to the owning dashboard user: two users of the
same tenant each see only their own conversations. Both repositories inherit
the Wall from TenantScopedRepository — no method can read across tenants.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from app.db.models import ChatMessage, ChatSession
from app.repositories.base import TenantScopedRepository


class ChatSessionRepository(TenantScopedRepository[ChatSession]):
    model = ChatSession

    async def get_for_user(
        self, tenant_id: UUID, user_id: UUID, session_id: UUID
    ) -> ChatSession | None:
        """One session, scoped to tenant AND owning user — a foreign or another
        user's session id misses (the caller turns that into a 404)."""
        stmt = self._require_tenant_scope(tenant_id).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_user(self, tenant_id: UUID, user_id: UUID) -> Sequence[ChatSession]:
        """This user's conversations, most recently active first."""
        stmt = (
            self._require_tenant_scope(tenant_id)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.updated_at.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()


class ChatMessageRepository(TenantScopedRepository[ChatMessage]):
    model = ChatMessage

    async def list_for_session(self, tenant_id: UUID, session_id: UUID) -> Sequence[ChatMessage]:
        """A session's messages in insertion order (seq — see the model note on
        why created_at can't order bubbles)."""
        stmt = (
            self._require_tenant_scope(tenant_id)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count_owner_messages_since(self, tenant_id: UUID, since: datetime) -> int:
        """Owner-sent bubbles since `since` — the free-plan daily chat quota."""
        stmt = (
            select(func.count())
            .select_from(ChatMessage)
            .where(
                ChatMessage.tenant_id == tenant_id,
                ChatMessage.role == "owner",
                ChatMessage.created_at >= since,
            )
        )
        return (await self._session.execute(stmt)).scalar_one()
