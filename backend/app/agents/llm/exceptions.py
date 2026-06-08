"""LLM-layer exceptions."""


class LLMUnavailable(RuntimeError):
    """All configured LLM providers failed to respond.

    Raised when LangChain's fallback chain exhausts every provider. Agent
    layers catch this and surface a graceful "AI temporarily unavailable"
    reply to the owner rather than propagating a 500 error.
    """
