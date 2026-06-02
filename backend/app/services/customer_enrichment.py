"""Customer name enrichment from message text.

Phase 1 auto-creates the customer with the WhatsApp display_name. Phase 2 enriches
it: when a customer states their name ("أنا أبو خالد", "اسمي سارة"), update
display_name — but TRACK the change, never trust it blindly (ROADMAP pitfall:
names update; keep a trail). A light heuristic (no extra LLM call) covers the
common Lebanese self-introduction patterns; the change is audit-logged.
"""

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer
from app.infra.logging import get_logger
from app.repositories.customers import CustomerRepository
from app.services.audit import AuditService

log = get_logger(__name__)

# Self-introduction openers, Lebanese Arabic. The captured group is the name —
# kept short (1–3 words) so a whole sentence isn't swallowed as a "name".
_NAME_PATTERNS = [
    re.compile(r"\bاسمي\s+(?P<name>\S+(?:\s+\S+){0,2})"),
    re.compile(r"\bأنا\s+(?P<name>\S+(?:\s+\S+){0,2})"),
    re.compile(r"\bانا\s+(?P<name>\S+(?:\s+\S+){0,2})"),
    re.compile(r"\bمعك\s+(?P<name>\S+(?:\s+\S+){0,2})"),
    re.compile(r"\bمعكن\s+(?P<name>\S+(?:\s+\S+){0,2})"),
]

# Stop-words: a name run ends at the first of these. They mark the message moving
# from an introduction into an order/other clause ("اسمي ربيع بدي ..." → "ربيع").
_STOP_WORDS = {
    "بدي",
    "بدّي",
    "عم",
    "رح",
    "ما",
    "مش",
    "كتير",
    "هون",
    "بعتلك",
    "و",
    "ومعك",
    "اليوم",
    "لو",
    "بكرا",
    "من",
}


def extract_name(text: str | None) -> str | None:
    """Return a stated name from the message, or None. Conservative: fires only on
    a clear opener, then truncates the name run at the first stop-word so an order
    clause after the name is not swallowed ("اسمي ربيع بدي ٥ كعكات" → "ربيع")."""
    if not text:
        return None
    for pattern in _NAME_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        words: list[str] = []
        for word in m.group("name").split():
            cleaned = word.strip(" .،؟!")
            if not cleaned or cleaned in _STOP_WORDS:
                break
            words.append(cleaned)
        if not words:
            continue
        return " ".join(words)
    return None


class CustomerEnrichmentService:
    """Updates a customer's display_name from a stated name, tenant-scoped and
    audit-logged. A no-op when no name is stated or it already matches."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enrich_from_message(
        self, *, tenant_id: UUID, customer: Customer, text: str | None
    ) -> str | None:
        stated = extract_name(text)
        if stated is None or stated == customer.display_name:
            return None

        repo = CustomerRepository(self._session)
        # Re-load within tenant scope — never trust the passed instance to be in scope.
        row = await repo.get(tenant_id, customer.id)
        if row is None:
            return None
        previous = row.display_name
        row.display_name = stated
        await self._session.flush()
        await AuditService(self._session).record(
            tenant_id=tenant_id,
            actor_id=row.id,
            action="customer.name_updated",
            target=f"{previous or ''} -> {stated}",
        )
        await self._session.commit()
        log.info(
            "customer.enriched",
            tenant_id=str(tenant_id),
            customer_id=str(row.id),
        )
        return stated
