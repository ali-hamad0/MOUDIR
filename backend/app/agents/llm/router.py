"""Provider-agnostic LLM router.

The constitution forbids application code from importing a provider SDK directly:
every LLM call goes through this router. Provider SDKs are imported ONLY in their
dedicated adapter modules (gemini_router via this file, grok_router.py,
anthropic_router.py) and nowhere else in `app/` — CI guards that boundary.

Phase 2 shipped a single GeminiRouter. Phase 7 adds FallbackLLMRouter, which
chains [Gemini → Grok → Claude] behind the same LLMRouter Protocol — callers
see no change. Adding a provider is a change in the provider list passed to
FallbackLLMRouter, never in any agent, service, or tool.
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
    """Gemini-only router (the primary provider).

    The API key is read from settings (resolved from Vault at startup), never
    from a literal or env. `langchain_google_genai` is imported here and nowhere
    else in `app/` — the CI guard enforces this boundary.
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


class FallbackLLMRouter:
    """Phase 7 router: tries providers in order, falls back on any API error.

    Built in lifespan from [GeminiRouter, GrokRouter?, AnthropicRouter?] (optional
    providers are skipped when their key is absent). Uses LangChain's built-in
    `.with_fallbacks()` so the returned model transparently retries the next
    provider on RateLimitError, TimeoutError, or any other API failure.

    Callers receive a standard BaseChatModel (or RunnableWithFallbacks, which
    implements the same interface) — they never see the fallback logic.
    """

    def __init__(self, providers: list[LLMRouter]) -> None:
        if not providers:
            raise ValueError("FallbackLLMRouter requires at least one provider")
        self._providers = providers

    def tier1(self) -> BaseChatModel:
        return self._chain("tier1")

    def tier2(self) -> BaseChatModel:
        return self._chain("tier2")

    def _chain(self, tier: str) -> BaseChatModel:
        models = [getattr(p, tier)() for p in self._providers]
        if len(models) == 1:
            return models[0]
        # with_fallbacks: primary is tried first; on any exception, the next
        # model in the list is tried. If all fail, the last exception propagates.
        return models[0].with_fallbacks(
            models[1:],
            exceptions_to_handle=(Exception,),
        )
