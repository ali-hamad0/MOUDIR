"""SupervisorState for the OwnerSupervisor LangGraph (Task 7.6).

All fields are primitives (str, not UUID / ORM objects) so the state is
JSON-serializable for Postgres checkpoint persistence (AD-7.2). The caller
converts UUID ↔ str at the handle() boundary — never inside the graph.
"""

from typing import TypedDict


class SupervisorState(TypedDict, total=False):
    message: str  # the owner's inbound message
    tenant_id: str  # UUID as string (JSON-safe for checkpoint)
    session_id: str  # conversation session UUID (forms the thread_id with tenant_id)
    intent: str  # classified intent: order|inventory|finance|customer|advisor
    routed_to: str  # which agent node handled this turn (same value as intent)
    response: str  # the specialist agent's Lebanese Arabic reply
