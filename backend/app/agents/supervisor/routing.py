"""Intent classification for the OwnerSupervisor (Task 7.6 / AD-7.8).

`classify_intent` is the only LLM call in the route node. It maps an owner's
freeform Arabic message to one of five intent classes. On any LLM failure or
parse error it defaults to "advisor" — never crashes the supervisor.

Two-layer tool-isolation enforcement (AD-7.8):

  Layer 1 — dispatcher role gate (MessageDispatcher.dispatch):
    The dispatcher checks `identity.role` BEFORE the supervisor is ever reached.
    - role == "customer" → OrderAgent directly; supervisor is never called.
    - role == "owner"    → OwnerSupervisor; OrderAgent customer tools are never reached.
    This is the first allowlist gate and is tested in test_dispatcher.py /
    test_tool_allowlists.py.

  Layer 2 — per-agent graph topology (this module + agent.py):
    `classify_intent` is called exclusively inside the supervisor's `route` node,
    which is only reachable via the owner path. Each specialist sub-graph is a
    separately compiled StateGraph whose node set is exactly that agent's tools.
    No edge exists from FinanceAgent to `queue_reengagement`; no edge from
    CustomerAgent to `check_stock`. The graph topology IS the allowlist —
    enforced structurally, not by string matching. See test_tool_allowlists.py.

Kept in its own module so it can be tested in isolation (mocking the LLM) and
so the golden-eval script (Task 7.10) can import it directly.
"""

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.infra.logging import get_logger
from prompts import supervisor_ar

log = get_logger(__name__)

Intent = Literal["order", "inventory", "finance", "customer", "advisor"]

_VALID_INTENTS: frozenset[str] = frozenset(Intent.__args__)  # type: ignore[attr-defined]

_DEFAULT_INTENT: Intent = "advisor"


class _IntentClassification(BaseModel):
    intent: Intent


# Whole-word confirmation heuristics for the pending stock-edit gate (Phase 10).
# Deterministic and LLM-free: a confirmation must never be hallucinated. Checked
# on the NORMALIZED first token only — "نعم، وكمان شو ناقص؟" still counts as yes;
# anything that doesn't open with a clear yes/no is treated as a new message
# (the stale proposal is dropped and normal intent routing takes over).
_YES_WORDS = frozenset(
    {
        "نعم",
        "اي",
        "ايه",
        "ايوا",
        "ايوه",
        "اكيد",
        "تمام",
        "موافق",
        "ماشي",
        "اوك",
        "اوكي",
        "yes",
        "ok",
    }
)
_NO_WORDS = frozenset({"لا", "كلا", "مش", "لغي", "الغي", "الغاء", "بطل", "no"})


def _normalize_token(token: str) -> str:
    token = token.strip(" .،,!؟?؛:«»\"'")
    return token.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").lower()


def parse_confirmation(message: str) -> Literal["yes", "no"] | None:
    """Read a yes/no off the message's first token, or None when it is neither."""
    tokens = message.split()
    if not tokens:
        return None
    first = _normalize_token(tokens[0])
    if first in _YES_WORDS:
        return "yes"
    if first in _NO_WORDS:
        return "no"
    return None


async def classify_intent(message: str, llm) -> Intent:
    """Classify the owner's message into one of five intent classes.

    `llm` is the raw Tier-1 LangChain chat model (from `router.tier1()`),
    NOT the LLMRouter — this keeps the function independently testable.

    Returns `"advisor"` on any failure (parse error, provider error, invalid
    intent string). Never raises.
    """
    model = llm.with_structured_output(_IntentClassification)
    system = supervisor_ar.CLASSIFY_SYSTEM.format(message=message)
    try:
        result: _IntentClassification = await model.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=message)]
        )
        intent = result.intent
        if intent not in _VALID_INTENTS:
            log.warning(
                "supervisor.classify_intent.invalid",
                intent=intent,
                fallback=_DEFAULT_INTENT,
            )
            return _DEFAULT_INTENT
        log.info("supervisor.classify_intent", intent=intent)
        return intent
    except Exception as exc:
        log.warning(
            "supervisor.classify_intent.error",
            error_type=type(exc).__name__,
            fallback=_DEFAULT_INTENT,
        )
        return _DEFAULT_INTENT
