"""Conversation session ID helpers (Task 7.7).

A conversation session groups one owner's messages into a single LangGraph
checkpoint thread. The session_id, combined with tenant_id via make_thread_id,
forms the checkpoint key (AD-7.2):

  thread_id = f"{tenant_id}:{session_id}"

Using the actor's DB primary key as the session_id gives each owner a stable,
continuous conversation thread — appropriate for a WhatsApp chatbot where the
owner expects the supervisor to remember context across messages. A different
owner on the same tenant gets a different session (different actor.id).

The Wall is preserved at the thread_id level: even though session_id is the
actor UUID (not a random value), tenant_id is always prepended by make_thread_id
so a checkpoint for Tenant A can never collide with one for Tenant B.
"""

from app.domain.identity import ResolvedIdentity


def make_session_id(identity: ResolvedIdentity) -> str:
    """Derive a stable conversation session ID from the caller's actor UUID.

    For owners, `identity.actor` is a `TenantOwner` whose `.id` is stable —
    so every message from the same owner resumes the same LangGraph thread and
    the supervisor can carry context across WhatsApp turns. The checkpoint key
    is formed by `make_thread_id(tenant_id, session_id)` — call that, not this,
    when building the thread_id for LangGraph config.
    """
    return str(identity.actor.id)
