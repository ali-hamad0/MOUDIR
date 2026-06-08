"""LLM model pricing — approximate cost per 1,000 tokens (input, output).

Used by the cost-tracking callback (Task 7.9) to estimate per-query cost.
All figures in USD. Sourced from provider pricing pages as of 2026-06; prices
change and enterprise/custom contracts may differ.
"""

# (input_per_1k_usd, output_per_1k_usd) — approximate.
COST_PER_1K_TOKENS: dict[str, tuple[float, float]] = {
    # Gemini 2.5 Flash — Tier 1 default
    "gemini-2.5-flash": (0.00015, 0.00060),
    # Gemini 2.5 Pro — Tier 2 default
    "gemini-2.5-pro": (0.00125, 0.01000),
    # Grok 3 Mini — Tier 1 first fallback
    "grok-3-mini": (0.00030, 0.00050),
    # Grok 3 — Tier 2 first fallback
    "grok-3": (0.00300, 0.01500),
    # Claude Haiku 4.5 — Tier 1 emergency fallback
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    # Claude Sonnet 4.6 — Tier 2 emergency fallback
    "claude-sonnet-4-6": (0.00300, 0.01500),
}

# Model name aliases — the LangChain serializer may emit "models/gemini-*" prefixed names.
COST_PER_1K_TOKENS["models/gemini-2.5-flash"] = COST_PER_1K_TOKENS["gemini-2.5-flash"]
COST_PER_1K_TOKENS["models/gemini-2.5-pro"] = COST_PER_1K_TOKENS["gemini-2.5-pro"]


def estimate_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return estimated cost in USD. Returns 0.0 for unknown model names."""
    rates = COST_PER_1K_TOKENS.get(model_name)
    if rates is None:
        return 0.0
    return rates[0] * prompt_tokens / 1000 + rates[1] * completion_tokens / 1000


# Alias used by CostTrackingCallback (Task 7.9).
compute_cost = estimate_cost
