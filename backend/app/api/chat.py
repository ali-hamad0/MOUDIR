"""Owner chat API — Task 7.13, saved sessions in Phase 10.

POST /chat: legacy single-thread endpoint — routes one message through the
OwnerSupervisor on the owner's stable per-user thread (kept for compatibility).

The /chat/sessions endpoints add saved conversations (Phase 10): each session
is a persisted chat with full message history AND its own LangGraph thread, so
multi-turn state (e.g. a pending stock-edit «نعم/لا») is scoped per
conversation.

Auth: get_current_user (JWT, owner only). No webhook format — clean browser-
facing JSON endpoints so the frontend never touches WhatsApp webhook payloads.
"""

import base64
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db_session
from app.infra.checkpointer import make_thread_id
from app.repositories.chat import ChatMessageRepository
from app.services.chat import ChatService
from app.services.plan_gate import (
    FREE_MAX_CHAT_MESSAGES_PER_DAY,
    require_pro,
    require_within_limit,
    start_of_today,
)

router = APIRouter(prefix="/chat", tags=["chat"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    agent: str  # routing decision: order|inventory|finance|customer|advisor
    session_id: str


class ChatSessionOut(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    role: str  # "owner" | "modir"
    content: str
    agent: str | None
    created_at: datetime


class VoiceChatResponse(BaseModel):
    transcript: str  # what the owner said (their bubble)
    response: str  # Modir's reply text (the modir bubble)
    agent: str
    session_id: str
    audio_b64: str  # the spoken reply, base64 WAV — small enough for JSON
    audio_mime: str


@router.post("", response_model=ChatResponse)
async def owner_chat(
    body: ChatRequest,
    current_user: CurrentUser,
    request: Request,
    db: Db,
) -> ChatResponse:
    """Route one owner message through the OwnerSupervisor.

    Returns the Lebanese Arabic reply and which specialist agent handled it
    (for the badge displayed in the chat UI). The agent is read from the
    LangGraph checkpoint after the run so the route node's decision is always
    reflected — even when the graph resumes from a prior checkpoint.
    """
    # Same free-plan daily quota as the sessions endpoint — the legacy route
    # must not be a quota bypass.
    sent_today = await ChatMessageRepository(db).count_owner_messages_since(
        current_user.tenant_id, start_of_today()
    )
    await require_within_limit(
        db, current_user.tenant_id, "chat", sent_today, FREE_MAX_CHAT_MESSAGES_PER_DAY
    )
    supervisor = request.app.state.supervisor
    checkpointer = request.app.state.checkpointer

    tenant_id: UUID = current_user.tenant_id
    session_id = str(current_user.id)  # stable per owner (mirrors make_session_id)
    thread_id = make_thread_id(tenant_id, session_id)
    config = {"configurable": {"thread_id": thread_id}}

    response = await supervisor.handle(body.message, tenant_id, session_id)

    # Read the checkpoint to learn which agent handled this turn.
    agent = "advisor"  # default when checkpoint is absent or routed_to is missing
    checkpoint_tuple = await checkpointer.aget_tuple(config)
    if checkpoint_tuple:
        channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        agent = channel_values.get("routed_to", "advisor")

    return ChatResponse(response=response, agent=agent, session_id=session_id)


# ── Saved sessions (Phase 10) ─────────────────────────────────────────────────


@router.post("/sessions", response_model=ChatSessionOut)
async def create_chat_session(current_user: CurrentUser, db: Db) -> ChatSessionOut:
    """Start a new saved conversation."""
    row = await ChatService(db).create_session(
        tenant_id=current_user.tenant_id, user_id=current_user.id
    )
    return ChatSessionOut.model_validate(row, from_attributes=True)


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_chat_sessions(current_user: CurrentUser, db: Db) -> list[ChatSessionOut]:
    """This user's conversations, most recently active first."""
    rows = await ChatService(db).list_sessions(
        tenant_id=current_user.tenant_id, user_id=current_user.id
    )
    return [ChatSessionOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def list_chat_messages(
    session_id: UUID, current_user: CurrentUser, db: Db
) -> list[ChatMessageOut]:
    """A conversation's full history, oldest first. 404 if it isn't this user's."""
    rows = await ChatService(db).list_messages(
        tenant_id=current_user.tenant_id, user_id=current_user.id, session_id=session_id
    )
    return [ChatMessageOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/sessions/{session_id}/messages", response_model=ChatResponse)
async def send_chat_message(
    session_id: UUID,
    body: ChatRequest,
    current_user: CurrentUser,
    db: Db,
    request: Request,
) -> ChatResponse:
    """Send one message inside a saved conversation and get Modir's reply.

    Both bubbles are persisted; the supervisor runs on THIS session's thread,
    so confirmations and other multi-turn state stay inside the conversation.
    """
    # Free plan: a daily chat quota (plan_gate) — Pro is unlimited.
    sent_today = await ChatMessageRepository(db).count_owner_messages_since(
        current_user.tenant_id, start_of_today()
    )
    await require_within_limit(
        db, current_user.tenant_id, "chat", sent_today, FREE_MAX_CHAT_MESSAGES_PER_DAY
    )
    reply, agent = await ChatService(db).send_message(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        session_id=session_id,
        message=body.message,
        supervisor=request.app.state.supervisor,
        checkpointer=request.app.state.checkpointer,
    )
    return ChatResponse(response=reply, agent=agent, session_id=str(session_id))


@router.post("/sessions/{session_id}/voice", response_model=VoiceChatResponse)
async def send_voice_message(
    session_id: UUID,
    audio: UploadFile,
    current_user: CurrentUser,
    db: Db,
    request: Request,
) -> VoiceChatResponse:
    """One push-to-talk turn: spoken audio in, spoken reply out.

    transcribe (Gemini STT, same component as WhatsApp voice notes) → the
    supervisor on this session's thread (both bubbles persisted as text, so a
    voice turn reads like any other in the history) → Gemini TTS for the reply.
    The audio returns base64-in-JSON — replies are a few seconds of speech, so
    payloads stay small and the frontend can hand a data URL straight to
    <audio>.
    """
    await require_pro(db, current_user.tenant_id, "voice")  # voice chat is Pro-only
    audio_bytes = await audio.read()
    mime_type = audio.content_type or "audio/webm"
    transcript = await request.app.state.audio_transcriber.transcribe(audio_bytes, mime_type)

    reply, agent = await ChatService(db).send_message(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        session_id=session_id,
        message=transcript,
        supervisor=request.app.state.supervisor,
        checkpointer=request.app.state.checkpointer,
    )

    wav, wav_mime = await request.app.state.speech_synthesizer.synthesize(reply)
    return VoiceChatResponse(
        transcript=transcript,
        response=reply,
        agent=agent,
        session_id=str(session_id),
        audio_b64=base64.b64encode(wav).decode(),
        audio_mime=wav_mime,
    )
