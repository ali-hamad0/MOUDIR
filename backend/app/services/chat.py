"""Owner chat sessions service (Phase 10).

Persists dashboard conversations: one ChatSession row per conversation, one
ChatMessage row per bubble. Each session's id IS the supervisor session_id, so
every conversation gets its own LangGraph thread (and its own pending
stock-edit confirmation state) under make_thread_id's tenant prefix.

Everything is scoped to (tenant_id, user_id) through the repositories — the
Wall, plus per-user privacy inside a tenant.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, ChatSession
from app.infra.checkpointer import make_thread_id
from app.infra.logging import get_logger
from app.repositories.chat import ChatMessageRepository, ChatSessionRepository
from prompts import chat_ar

log = get_logger(__name__)

_TITLE_MAX = 120


class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._sessions = ChatSessionRepository(session)
        self._messages = ChatMessageRepository(session)

    async def create_session(self, *, tenant_id: UUID, user_id: UUID) -> ChatSession:
        row = await self._sessions.add(tenant_id, ChatSession(user_id=user_id))
        await self._session.commit()
        log.info("chat.session.created", tenant_id=str(tenant_id), session_id=str(row.id))
        return row

    async def list_sessions(self, *, tenant_id: UUID, user_id: UUID) -> list[ChatSession]:
        return list(await self._sessions.list_for_user(tenant_id, user_id))

    async def list_messages(
        self, *, tenant_id: UUID, user_id: UUID, session_id: UUID
    ) -> list[ChatMessage]:
        await self._require_session(tenant_id, user_id, session_id)
        return list(await self._messages.list_for_session(tenant_id, session_id))

    async def send_message(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        session_id: UUID,
        message: str,
        supervisor,
        checkpointer,
    ) -> tuple[str, str]:
        """Run one owner turn in a saved conversation. Returns (reply, agent).

        The owner message is persisted BEFORE the supervisor runs (a supervisor
        hiccup must not lose what the owner typed); the reply is persisted after.
        The agent badge is read from the LangGraph checkpoint, same as the
        legacy /chat endpoint.
        """
        chat = await self._require_session(tenant_id, user_id, session_id)

        await self._messages.add(
            tenant_id, ChatMessage(session_id=session_id, role="owner", content=message)
        )
        if chat.title is None and message.strip():
            chat.title = message.strip()[:_TITLE_MAX]
        # Touch the session so the sidebar orders by last activity.
        chat.updated_at = datetime.now(UTC)
        await self._session.commit()

        reply = await supervisor.handle(message, tenant_id, str(session_id))

        agent = "advisor"
        checkpoint_tuple = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": make_thread_id(tenant_id, str(session_id))}}
        )
        if checkpoint_tuple:
            channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
            agent = channel_values.get("routed_to", "advisor")

        await self._messages.add(
            tenant_id,
            ChatMessage(session_id=session_id, role="modir", content=reply, agent=agent),
        )
        await self._session.commit()
        log.info(
            "chat.message.handled",
            tenant_id=str(tenant_id),
            session_id=str(session_id),
            agent=agent,
        )
        return reply, agent

    async def _require_session(
        self, tenant_id: UUID, user_id: UUID, session_id: UUID
    ) -> ChatSession:
        chat = await self._sessions.get_for_user(tenant_id, user_id, session_id)
        if chat is None:
            # A cross-tenant or another user's id misses the scoped lookup → 404.
            raise HTTPException(status.HTTP_404_NOT_FOUND, chat_ar.SESSION_NOT_FOUND)
        return chat
