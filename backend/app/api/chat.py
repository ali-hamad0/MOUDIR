"""Owner chat API — Task 7.13.

POST /chat: accepts an owner message, routes through the OwnerSupervisor,
returns the Arabic reply and which specialist agent handled it.

Auth: get_current_user (JWT, owner only). No webhook format — clean browser-
facing JSON endpoint so the frontend never touches WhatsApp webhook payloads.

The session_id is the owner's user ID (stable per owner). This mirrors
make_session_id() in app.infra.session — each owner gets one continuous
LangGraph thread per deployment, surviving container restarts via the Postgres
checkpointer.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.db.models import User
from app.infra.checkpointer import make_thread_id

router = APIRouter(prefix="/chat", tags=["chat"])

CurrentUser = Annotated[User, Depends(get_current_user)]


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    agent: str  # routing decision: order|inventory|finance|customer|advisor
    session_id: str


@router.post("", response_model=ChatResponse)
async def owner_chat(
    body: ChatRequest,
    current_user: CurrentUser,
    request: Request,
) -> ChatResponse:
    """Route one owner message through the OwnerSupervisor.

    Returns the Lebanese Arabic reply and which specialist agent handled it
    (for the badge displayed in the chat UI). The agent is read from the
    LangGraph checkpoint after the run so the route node's decision is always
    reflected — even when the graph resumes from a prior checkpoint.
    """
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
