"""LangChain callback that writes one AgentRun row per LLM call (Task 7.9 / AD-7.7).

Attached to the supervisor's ainvoke config so every LLM call inside the
supervisor graph (including the classify_intent call in the route node) is
captured. The callback:

  1. Records the model name in on_llm_start (the serialized dict carries it).
  2. Reads token counts from response.llm_output in on_llm_end.
  3. Estimates USD cost via pricing.compute_cost.
  4. Writes one AgentRun row to Postgres inside its own session.
  5. NEVER raises — a failed cost row must not fail the user's request.

on_llm_end token_usage key names vary by provider:
  - Gemini (langchain_google_genai): "usage_metadata" with keys
    "prompt_token_count" / "candidates_token_count"
  - OpenAI-compatible (Grok): "token_usage" with keys "prompt_tokens" /
    "completion_tokens"
  - Anthropic: "usage" with keys "input_tokens" / "output_tokens"
The helper _extract_tokens handles all three; unknown structures → 0 (no crash).
"""

from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.llm.pricing import compute_cost
from app.infra.logging import get_logger
from app.repositories.agent_runs import AgentRunRepository

log = get_logger(__name__)


def _extract_tokens(llm_output: dict | None) -> tuple[int, int]:
    """Return (prompt_tokens, completion_tokens) from an llm_output dict.

    Returns (0, 0) on any missing or malformed structure — a missing cost entry
    is not a reason to crash.
    """
    if not llm_output:
        return 0, 0

    # Gemini via langchain_google_genai
    meta = llm_output.get("usage_metadata") or {}
    if meta:
        return (
            int(meta.get("prompt_token_count", 0)),
            int(meta.get("candidates_token_count", 0)),
        )

    # OpenAI-compatible (Grok, older Gemini compat)
    usage = llm_output.get("token_usage") or {}
    if usage:
        return (
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

    # Anthropic SDK
    usage2 = llm_output.get("usage") or {}
    if usage2:
        return (
            int(usage2.get("input_tokens", 0)),
            int(usage2.get("output_tokens", 0)),
        )

    return 0, 0


class CostTrackingCallback(AsyncCallbackHandler):
    """Write one AgentRun row per LLM call. Thread-safe: no shared mutable state
    between concurrent invocations (each call creates a fresh session).

    Parameters
    ----------
    session_factory:
        An async_sessionmaker that opens a new session per call. The callback
        commits its own session so the cost row is durable even if the caller
        rolls back later.
    tenant_id:
        The tenant whose run this is. Written to every AgentRun row (The Wall).
    agent_name:
        Short label for the calling agent — "supervisor", "finance", etc.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        tenant_id: UUID,
        agent_name: str,
    ) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._agent_name = agent_name
        self._model_name: str = "unknown"

    async def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        # Extract model name from the serialized LangChain component dict.
        # kwargs may carry "invocation_params" with a "model" key for some providers.
        invocation_params = kwargs.get("invocation_params") or {}
        model = (
            invocation_params.get("model")
            or serialized.get("kwargs", {}).get("model")
            or serialized.get("name", "unknown")
        )
        self._model_name = str(model)

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            prompt_tokens, completion_tokens = _extract_tokens(response.llm_output)
            cost_usd = compute_cost(self._model_name, prompt_tokens, completion_tokens)
            async with self._session_factory() as session:
                await AgentRunRepository(session).create(
                    tenant_id=self._tenant_id,
                    agent_name=self._agent_name,
                    model_name=self._model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost_usd,
                )
                await session.commit()
            log.info(
                "cost_callback.recorded",
                tenant_id=str(self._tenant_id),
                agent_name=self._agent_name,
                model_name=self._model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
            )
        except Exception as exc:
            log.warning(
                "cost_callback.on_llm_end.error",
                tenant_id=str(self._tenant_id),
                agent_name=self._agent_name,
                error_type=type(exc).__name__,
                error=str(exc),
            )
