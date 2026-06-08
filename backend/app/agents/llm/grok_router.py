"""Grok LLM adapter (OpenAI-compatible API at api.x.ai/v1).

`langchain_openai` is imported HERE and nowhere else in `app/` — the same
provider-quarantine pattern as the Gemini router. The CI guard (Task 7.14)
enforces that boundary with an AST-based grep test.
"""

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.infra.settings import Settings


class GrokRouter:
    """Grok provider via the OpenAI-compatible API (api.x.ai/v1).

    Tier 1 = grok-3-mini  (fast, cheap — replaces Gemini Flash as fallback).
    Tier 2 = grok-3       (stronger — replaces Gemini Pro as fallback).

    Only instantiated by FallbackLLMRouter when grok_api_key is non-empty.
    """

    _BASE_URL = "https://api.x.ai/v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _build(self, model: str) -> BaseChatModel:
        return ChatOpenAI(
            base_url=self._BASE_URL,
            api_key=self._settings.grok_api_key.get_secret_value(),
            model=model,
            temperature=0,
        )

    def tier1(self) -> BaseChatModel:
        return self._build(self._settings.grok_tier1_model)

    def tier2(self) -> BaseChatModel:
        return self._build(self._settings.grok_tier2_model)
