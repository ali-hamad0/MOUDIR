"""Task 7.1 — LLM Router fallback chain tests.

Tests cover:
- FallbackLLMRouter chains providers correctly via with_fallbacks()
- Empty provider list is rejected at construction
- Pricing utilities return sane values
- Provider-SDK quarantine: langchain_openai / langchain_anthropic / langchain_google_genai
  are each imported only in their dedicated adapter module, nowhere else in app/
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.llm.exceptions import LLMUnavailable
from app.agents.llm.pricing import COST_PER_1K_TOKENS, estimate_cost
from app.agents.llm.router import FallbackLLMRouter, LLMRouter
from app.infra.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://x:x@localhost/x",
        "redis_url": "redis://localhost:6379",
        "vault_addr": "http://localhost:8200",
        "vault_token": "root",
    }
    base.update(overrides)
    return Settings(**base)


def _mock_router() -> LLMRouter:
    """Return a mock LLMRouter whose tier methods return mock models."""
    router = MagicMock(spec=LLMRouter)
    router.tier1.return_value = MagicMock()
    router.tier2.return_value = MagicMock()
    return router


# ---------------------------------------------------------------------------
# FallbackLLMRouter
# ---------------------------------------------------------------------------


class TestFallbackLLMRouter:
    def test_empty_providers_raises(self):
        with pytest.raises(ValueError):
            FallbackLLMRouter([])

    def test_single_provider_returns_model_directly(self):
        primary = _mock_router()
        router = FallbackLLMRouter([primary])

        model = router.tier1()

        primary.tier1.assert_called_once()
        assert model is primary.tier1.return_value

    def test_two_providers_attaches_fallback(self):
        primary = _mock_router()
        fallback = _mock_router()
        # with_fallbacks is called on the primary model
        primary.tier1.return_value.with_fallbacks = MagicMock()

        router = FallbackLLMRouter([primary, fallback])
        router.tier1()

        primary.tier1.return_value.with_fallbacks.assert_called_once()
        chained = primary.tier1.return_value.with_fallbacks.call_args[0][0]
        assert len(chained) == 1
        assert chained[0] is fallback.tier1.return_value

    def test_three_providers_chains_both_fallbacks(self):
        primary = _mock_router()
        fb1 = _mock_router()
        fb2 = _mock_router()
        primary.tier1.return_value.with_fallbacks = MagicMock()

        router = FallbackLLMRouter([primary, fb1, fb2])
        router.tier1()

        chained = primary.tier1.return_value.with_fallbacks.call_args[0][0]
        assert len(chained) == 2
        assert chained[0] is fb1.tier1.return_value
        assert chained[1] is fb2.tier1.return_value

    def test_tier2_uses_tier2_from_each_provider(self):
        primary = _mock_router()
        fallback = _mock_router()
        primary.tier2.return_value.with_fallbacks = MagicMock()

        router = FallbackLLMRouter([primary, fallback])
        router.tier2()

        primary.tier2.assert_called_once()
        fallback.tier2.assert_called_once()

    def test_tier1_and_tier2_are_independent(self):
        """tier1() and tier2() each build their own model chain."""
        primary = _mock_router()
        primary.tier1.return_value.with_fallbacks = MagicMock()
        primary.tier2.return_value.with_fallbacks = MagicMock()
        fallback = _mock_router()

        router = FallbackLLMRouter([primary, fallback])
        router.tier1()
        router.tier2()

        primary.tier1.return_value.with_fallbacks.assert_called_once()
        primary.tier2.return_value.with_fallbacks.assert_called_once()


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


def test_llm_unavailable_is_runtime_error():
    exc = LLMUnavailable("all providers failed")
    assert isinstance(exc, RuntimeError)
    assert "all providers failed" in str(exc)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


class TestPricing:
    def test_known_model_returns_positive_cost(self):
        cost = estimate_cost("gemini-2.5-flash", 1000, 200)
        assert cost > 0

    def test_unknown_model_returns_zero(self):
        assert estimate_cost("no-such-model-xyz", 1000, 200) == 0.0

    def test_zero_tokens_returns_zero(self):
        assert estimate_cost("gemini-2.5-flash", 0, 0) == 0.0

    def test_all_configured_models_have_positive_rates(self):
        for model in COST_PER_1K_TOKENS:
            cost = estimate_cost(model, 1000, 100)
            assert cost > 0, f"Model {model!r} has zero estimated cost"

    def test_output_tokens_contribute_to_cost(self):
        cost_no_output = estimate_cost("gemini-2.5-flash", 1000, 0)
        cost_with_output = estimate_cost("gemini-2.5-flash", 1000, 500)
        assert cost_with_output > cost_no_output


# ---------------------------------------------------------------------------
# Settings — new Phase 7 fields
# ---------------------------------------------------------------------------


class TestSettingsPhase7:
    def test_grok_api_key_defaults_empty(self):
        s = _settings()
        assert s.grok_api_key.get_secret_value() == ""

    def test_anthropic_api_key_defaults_empty(self):
        s = _settings()
        assert s.anthropic_api_key.get_secret_value() == ""

    def test_grok_models_have_defaults(self):
        s = _settings()
        assert s.grok_tier1_model
        assert s.grok_tier2_model

    def test_anthropic_models_have_defaults(self):
        s = _settings()
        assert s.anthropic_tier1_model
        assert s.anthropic_tier2_model


# ---------------------------------------------------------------------------
# Provider-SDK quarantine
# ---------------------------------------------------------------------------


def _find_sdk_imports(package_prefix: str, exclude_filenames: list[str]) -> list[str]:
    """Return app/ files that import the given package outside excluded filenames."""
    root = Path(__file__).parent.parent / "app"
    violations = []
    for py_file in sorted(root.rglob("*.py")):
        if any(py_file.name == exc for exc in exclude_filenames):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if module.startswith(package_prefix):
                violations.append(str(py_file))
                break
    return violations


def test_langchain_openai_only_in_grok_router():
    violations = _find_sdk_imports("langchain_openai", ["grok_router.py"])
    assert violations == [], f"langchain_openai imported outside grok_router.py: {violations}"


def test_langchain_anthropic_only_in_anthropic_router():
    violations = _find_sdk_imports("langchain_anthropic", ["anthropic_router.py"])
    assert (
        violations == []
    ), f"langchain_anthropic imported outside anthropic_router.py: {violations}"


def test_langchain_google_genai_only_in_router_and_embeddings():
    violations = _find_sdk_imports("langchain_google_genai", ["router.py", "gemini_embeddings.py"])
    assert violations == [], f"langchain_google_genai imported outside allowed files: {violations}"
