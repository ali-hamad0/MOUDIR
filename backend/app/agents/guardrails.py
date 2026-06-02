"""Conversational guardrails (GUARDRAILS.md Layer 2).

Hand-rolled rails — the doc's leaning (control + auditability over a heavy
framework). These are PROBABILISTIC: they reduce the chance the LLM misbehaves on
free-text input. Layer 1 (the Wall, tenant-scoped tools, Pydantic-validated I/O)
is what makes any failure SAFE and is NOT replaced by these.

- Input rail  → injection / jailbreak / cross-tenant probing / off-topic.
                Tripped: the agent does NOT run; caller replies politely in
                Lebanese Arabic.
- Output rail → "no hallucinated catalog item" + PII redaction on the outgoing
                reply.

Every tripped rail is audit-logged with tenant_id (so attack rates are
measurable per tenant). Wiring into the dispatcher happens in Task 2.11.
"""

import re
from dataclasses import dataclass, field

# Phone-number-ish runs (Lebanese +961..., or any 7+ digit run). Redacted from
# outgoing text so a reply never echoes a number back (constitution III).
_PHONE_RE = re.compile(r"\+?\d[\d\s\-]{6,}\d")
_PHONE_REDACTION = "[رقم]"

# Injection / jailbreak / cross-tenant-probe markers, Arabic + English. Kept as
# substrings (lowercased compare) — deliberately simple and auditable. A real
# safety classifier (Llama Guard / Gemini) is the deferred framework decision in
# GUARDRAILS.md; this heuristic is the Phase 2 baseline.
_INJECTION_MARKERS: tuple[str, ...] = (
    # English jailbreak/injection phrasing
    "ignore your instructions",
    "ignore previous",
    "disregard your",
    "system prompt",
    "reveal your prompt",
    "show me all orders",
    "all customers",
    "other customers",
    "last customer",
    "act as",
    "you are now",
    "developer mode",
    # Arabic equivalents
    "تجاهل التعليمات",
    "تجاهل تعليماتك",
    "كل الطلبات",
    "كل الزباين",
    "كل الزبائن",
    "الزبون السابق",
    "اكشف",
    "التعليمات السرية",
)


@dataclass
class RailResult:
    """Outcome of a rail. `allowed=False` blocks the message / reply.

    `reason` names the tripped rail (for the audit log); `text` carries the
    (possibly redacted) text for output rails.
    """

    allowed: bool
    reason: str | None = None
    text: str | None = None
    matched: list[str] = field(default_factory=list)


def check_input(text: str | None) -> RailResult:
    """Input rail: refuse injection / jailbreak / cross-tenant probing.

    Returns allowed=False with a reason when a marker is found; the caller then
    replies with order_ar.RAIL_REFUSAL and does NOT run the agent.
    """
    if not text:
        return RailResult(allowed=True)
    lowered = text.lower()
    hits = [m for m in _INJECTION_MARKERS if m.lower() in lowered]
    if hits:
        return RailResult(allowed=False, reason="input.injection", matched=hits)
    return RailResult(allowed=True)


def redact_pii(text: str) -> str:
    """Strip phone-number-like runs from outgoing text."""
    return _PHONE_RE.sub(_PHONE_REDACTION, text)


def check_output(
    reply: str,
    catalog_names: set[str],
    *,
    referenced_names: set[str] | None = None,
) -> RailResult:
    """Output rail: reject a hallucinated catalog item, then redact PII.

    `catalog_names` — the real product names for this tenant.
    `referenced_names` — the product names this reply claims to have ordered
        (passed by the caller from the confirmed order). If ANY of them is not in
        `catalog_names`, the reply is hallucinating a product → blocked.

    Phase 2 confirmations are template-based and only reference catalog-validated
    items, so this rail should pass in normal operation; it is the backstop that
    makes "no hallucinated catalog item" an enforced output check, not just an
    upstream guarantee. When `referenced_names` is None there is nothing to verify
    (e.g. a refusal/clarification reply) and only PII redaction applies.
    """
    if referenced_names:
        hallucinated = sorted(referenced_names - catalog_names)
        if hallucinated:
            return RailResult(
                allowed=False, reason="output.hallucinated_item", matched=hallucinated
            )
    return RailResult(allowed=True, text=redact_pii(reply))
