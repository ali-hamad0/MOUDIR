"""Anthropic (Claude) LLM adapter — emergency fallback provider.

`langchain_anthropic` is imported HERE and nowhere else in `app/` — the same
provider-quarantine pattern as the Gemini router. The CI guard (Task 7.14)
enforces that boundary with an AST-based grep test.
"""

from langchain_core.language_models import BaseChatModel

from app.infra.settings import Settings


class AnthropicRouter:
    """Claude provider via the Anthropic API.

    Tier 1 = Claude Haiku  (fast, cheap — emergency fallback for classification).
    Tier 2 = Claude Sonnet (stronger — emergency fallback for reasoning tasks).

    Only instantiated by FallbackLLMRouter when anthropic_api_key is non-empty.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _build(self, model: str) -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic  # lazy: only when key is present

        return ChatAnthropic(
            api_key=self._settings.anthropic_api_key.get_secret_value(),
            model=model,
            temperature=0,
        )

    def tier1(self) -> BaseChatModel:
        return self._build(self._settings.anthropic_tier1_model)

    def tier1_json(self) -> BaseChatModel:
        return self.tier1()

    def tier2(self) -> BaseChatModel:
        return self._build(self._settings.anthropic_tier2_model)
