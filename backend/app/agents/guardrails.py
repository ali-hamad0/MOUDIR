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

# Phone-number-ish runs. Explicit Lebanese formats first, then the generic
# fallback so e.g. "+961 70 123456" and "03-123456" are all caught.
_PHONE_RE = re.compile(
    r"\+961[\s\-]?\d{1,2}[\s\-]?\d{6}"  # +961 X XXXXXX  (Lebanese intl.)
    r"|0\d[\-\s]\d{6}"  # 0X-XXXXXX  (Lebanese local)
    r"|\+?\d[\d\s\-]{6,}\d"  # generic international
)
_PHONE_REDACTION = "[رقم]"

_MAX_INPUT_LENGTH = 4000

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
    "system:",  # system-message injection via colon prefix
    "reveal your prompt",
    "show me all orders",
    "all customers",
    "other customers",
    "last customer",
    "act as",
    "you are now",
    "developer mode",
    # Arabic equivalents and transliterations
    "تجاهل التعليمات",
    "تجاهل تعليماتك",
    "تجاهل ما سبق",  # ignore what came before
    "تجاهل السابق",  # ignore previous (alt)
    "كل الطلبات",
    "كل الزباين",
    "كل الزبائن",
    "الزبون السابق",
    "اكشف",
    "التعليمات السرية",
    "نظام:",  # Arabic "system:" transliteration
    "أنت الآن",  # "you are now" in Arabic
    "تصرف كـ",  # "act as" in Arabic
    "وضع المطور",  # "developer mode" in Arabic
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


def check_input(text: str | None, role: str = "customer") -> RailResult:
    """Input rail: refuse injection / jailbreak / cross-tenant probing.

    `role` is recorded for audit; it does not change the blocking logic.
    Long inputs (≥ 4000 chars) are truncated before pattern matching — the
    truncated text is returned in `result.text` so the caller can use it.
    Returns allowed=False with a reason when an injection marker is found.
    """
    if not text:
        return RailResult(allowed=True)
    truncated = False
    if len(text) >= _MAX_INPUT_LENGTH:
        text = text[:_MAX_INPUT_LENGTH]
        truncated = True
    lowered = text.lower()
    hits = [m for m in _INJECTION_MARKERS if m.lower() in lowered]
    if hits:
        return RailResult(allowed=False, reason="input.injection", matched=hits)
    if truncated:
        return RailResult(allowed=True, reason="input.truncated", text=text)
    return RailResult(allowed=True)


def redact_pii(text: str) -> str:
    """Strip phone-number-like runs from outgoing text."""
    return _PHONE_RE.sub(_PHONE_REDACTION, text)


def check_output(
    text: str,
    agent_name: str,
    *,
    catalog_items: set[str] | None = None,
    referenced_names: set[str] | None = None,
) -> RailResult:
    """Output rail: reject a hallucinated catalog item, then redact PII.

    `agent_name` — which agent produced this reply (for audit logging).
    `catalog_items` — the real product names for this tenant. Required when
        `referenced_names` is provided.
    `referenced_names` — product names this reply claims to reference. If ANY
        is not in `catalog_items`, the reply is hallucinating a product → blocked.

    When `catalog_items` is None (Finance, Customer, Advisor agents have no
    per-call catalog context) only PII redaction applies. This is intentional:
    the hallucination check is the backstop for order confirmations; for analytic
    replies the LLM synthesises ML numbers, not product names.
    """
    if referenced_names and catalog_items is not None:
        hallucinated = sorted(referenced_names - catalog_items)
        if hallucinated:
            return RailResult(
                allowed=False, reason="output.hallucinated_item", matched=hallucinated
            )
    return RailResult(allowed=True, text=redact_pii(text))
