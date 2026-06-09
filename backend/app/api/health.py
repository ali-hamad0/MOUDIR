"""Phase 8 (Task 8.5) — AI health endpoint for the circuit breaker.

The FallbackLLMRouter tracks the last failure timestamp internally; this
endpoint surfaces it to the frontend so the AI-unavailable banner can appear.
No DB call — purely in-memory state, O(1).
"""

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health/ai")
async def ai_health(request: Request) -> dict:
    """Return whether the LLM router is currently considered healthy.

    Returns `{"available": true}` normally; `{"available": false}` if the last
    LLM call raised LLMUnavailable within the last 60 seconds.  The frontend
    polls this every 30s to show or hide the manual-entry banner.
    """
    llm_router = getattr(request.app.state, "llm_router", None)
    if llm_router is None:
        return {"available": True}
    return {"available": llm_router.is_healthy()}
