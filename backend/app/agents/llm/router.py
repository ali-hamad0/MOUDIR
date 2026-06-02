"""Provider-agnostic LLM router.

The constitution forbids application code from importing a provider SDK directly:
every LLM call goes through this router. The provider SDK
(`langchain_google_genai`) is imported HERE and nowhere else in `app/` — CI
guards that boundary (Task 2.15).

Phase 2 ships a single-provider router (Gemini Flash, Tier 1). Phase 7 swaps the
implementation for a fallback chain (Gemini -> Grok -> Claude) behind the same
`LLMRouter` Protocol, so adding a provider is a change here, not in any agent,
service, or tool.
"""

from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from app.infra.settings import Settings


class LLMRouter(Protocol):
    """The only way app code obtains a chat model.

    Callers depend on this Protocol, never on a concrete provider. Adding a
    provider or a fallback chain is a change in the implementation behind this
    interface — config-driven — never in the callers.
    """

    def tier1(self) -> BaseChatModel:
        """Tier 1 (cheap, fast) model — order parsing, classification."""
        ...

    def tier2(self) -> BaseChatModel:
        """Tier 2 (stronger) model — reserved for harder language work."""
        ...


class GeminiRouter:
    """Phase 2 router: Gemini only.

    Phase 7 replaces this with a fallback chain (Gemini -> Grok -> Claude) behind
    the same `LLMRouter` Protocol, with no caller changes. The API key is read
    from settings (resolved from Vault at startup), never from a literal or env.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _build(self, model: str) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=self._settings.gemini_api_key.get_secret_value(),
            temperature=0,
        )

    def tier1(self) -> BaseChatModel:
        # Tier 1 = Flash for parsing (constitution's tier rule; ROADMAP pitfall:
        # do not use Pro for the parse step — Flash is enough and cheaper).
        return self._build(self._settings.llm_tier1_model)

    def tier2(self) -> BaseChatModel:
        return self._build(self._settings.llm_tier2_model)
