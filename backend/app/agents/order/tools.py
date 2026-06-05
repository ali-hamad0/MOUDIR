"""OrderAgent tools: get_products, parse_order, confirm_order.

All three are tenant-scoped through a ToolContext — a tool cannot run outside a
tenant. I/O is Pydantic-validated; bad LLM output in parse_order triggers a retry
(up to settings.llm_max_retries), never a crash. confirm_order re-validates every
line against the live catalog (the final guard against a hallucinated product).
"""

import re
from dataclasses import dataclass
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm.router import LLMRouter
from app.agents.order.schemas import ConfirmedOrder, ParsedOrder, ParsedOrderItem, RawOrder
from app.db.models import Order
from app.domain.identity import ResolvedIdentity
from app.infra.embeddings import EmbeddingClient
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.repositories.products import ProductRepository
from app.repositories.vector_chunks import VectorChunkRepository
from app.services.orders import OrderService
from prompts import order_agent_ar

log = get_logger(__name__)


@dataclass
class ToolContext:
    """Everything a tool needs, bound to one tenant + actor. Passing this (rather
    than a bare session) makes it impossible to call a tool outside a tenant
    scope — the Wall holds at the tool boundary too."""

    session: AsyncSession
    identity: ResolvedIdentity
    router: LLMRouter
    settings: Settings
    # The RAG embedder for search_knowledge_base (Task 5.16). Optional so a context
    # built without RAG (older tests) still works; the tool no-ops without it.
    embedding_client: EmbeddingClient | None = None

    @property
    def tenant_id(self) -> UUID:
        return self.identity.tenant.id


class CatalogItem(BaseModel):
    """A product the customer may order — the agent's source of truth."""

    id: UUID
    name_ar: str
    price_lbp: int | None = None
    price_usd: float | None = None
    is_available: bool


async def get_products(ctx: ToolContext) -> list[CatalogItem]:
    """Read the tenant's catalog. Called FIRST; the agent cannot order anything
    this does not return."""
    products = await ProductRepository(ctx.session).list(ctx.tenant_id)
    catalog = [
        CatalogItem(
            id=p.id,
            name_ar=p.name_ar,
            price_lbp=p.price_lbp,
            price_usd=float(p.price_usd) if p.price_usd is not None else None,
            is_available=p.is_available,
        )
        for p in products
    ]
    log.info(
        "tool.get_products",
        tenant_id=str(ctx.tenant_id),
        role=ctx.identity.role,
        count=len(catalog),
    )
    return catalog


def _render_catalog(catalog: list[CatalogItem]) -> str:
    """Compact listing fed to the parse prompt. Only available items are offered."""
    lines = [
        f"- {item.id} | {item.name_ar} | {item.price_lbp or ''} ل.ل."
        for item in catalog
        if item.is_available
    ]
    return "\n".join(lines) if lines else "(لا يوجد منتجات متوفّرة)"


# Arabic normalization for the name match: drop diacritics/tatweel, unify alef
# and taa-marbuta variants, collapse whitespace. Lenient enough not to reject a
# real product on a trivial spelling wobble, strict enough that كعك ≠ بيتزا.
_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟـ]")


def _normalize_ar(text: str) -> str:
    text = _AR_DIACRITICS.sub("", text)
    text = (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ة", "ه")
        .replace("ى", "ي")
    )
    return " ".join(text.split()).strip()


def _match_phrase_to_catalog(phrase: str, catalog: list[CatalogItem]) -> CatalogItem | None:
    """Map a customer's raw product phrase to ONE available catalog item, in code.

    Matching is done HERE (not by the LLM) so the model cannot substitute a real
    product for an unknown request: an unmatched phrase yields None and is dropped.
    Strategy on normalized Arabic: exact equality, then containment either way,
    then word-overlap — lenient on spelling, strict on identity (بيتزا won't match
    كعك). Returns None if no available item matches.
    """
    p = _normalize_ar(phrase)
    if not p:
        return None
    p_words = set(p.split())
    for item in catalog:
        if not item.is_available:
            continue
        n = _normalize_ar(item.name_ar)
        if p == n or p in n or n in p:
            return item
        # Word overlap (handles "كعكات" plural vs "كعك", extra adjectives).
        if p_words & set(n.split()):
            return item
    return None


async def parse_order(
    ctx: ToolContext, text: str, catalog: list[CatalogItem]
) -> ParsedOrder | None:
    """Extract an order from Lebanese-Arabic text and resolve it to the catalog.

    The LLM returns only the customer's RAW phrases + quantities (`RawOrder`); it
    never picks a catalog id. Our code then matches each phrase to a real product.
    A phrase with no catalog match is dropped — so the model cannot substitute a
    real product for an unknown request. Returns None when nothing resolves or the
    LLM keeps failing after retries; the caller replies politely, never crashes.
    """
    model = ctx.router.tier1().with_structured_output(RawOrder)
    system = order_agent_ar.PARSE_ORDER_SYSTEM.format(catalog=_render_catalog(catalog))
    messages = [SystemMessage(content=system), HumanMessage(content=text)]

    attempts = ctx.settings.llm_max_retries + 1
    for attempt in range(attempts):
        try:
            raw: RawOrder = await model.ainvoke(messages)
        except (ValidationError, ValueError) as e:
            # Malformed structured output → retry, not crash.
            log.warning(
                "tool.parse_order.invalid",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error=str(e),
            )
            continue
        except Exception as e:
            # Provider / transport failure (auth, rate-limit, timeout, network).
            # The flow must NEVER 500 on the customer — degrade to a polite reply.
            # (Phase 8 hardening adds provider fallback in the router itself.)
            log.warning(
                "tool.parse_order.llm_error",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error_type=type(e).__name__,
                error=str(e),
            )
            continue

        # Resolve each raw phrase to a catalog id IN CODE. Unmatched phrases (an
        # item the shop does not sell) are dropped — this is the "no hallucinated
        # catalog item" guarantee, enforced server-side rather than in the prompt.
        items = []
        for line in raw.items:
            match = _match_phrase_to_catalog(line.product_phrase, catalog)
            if match is None:
                log.info(
                    "tool.parse_order.dropped_no_match",
                    tenant_id=str(ctx.tenant_id),
                    phrase=line.product_phrase,
                )
                continue
            items.append(ParsedOrderItem(product_id=match.id, quantity=line.quantity))

        if not items:
            log.info("tool.parse_order.no_catalog_items", tenant_id=str(ctx.tenant_id))
            return None
        log.info(
            "tool.parse_order.ok",
            tenant_id=str(ctx.tenant_id),
            items=len(items),
            attempt=attempt + 1,
        )
        return ParsedOrder(
            items=items,
            fulfillment_type=raw.fulfillment_type,
            requested_time_text=raw.requested_time_text,
            note=raw.note,
        )

    log.warning("tool.parse_order.exhausted", tenant_id=str(ctx.tenant_id))
    return None


async def confirm_order(ctx: ToolContext, parsed: ParsedOrder) -> Order:
    """Re-validate against the live catalog and write the order, tenant-scoped.

    The final guard: even if parsing slipped, OrderService re-checks existence and
    availability per line and refuses anything not in the catalog. Raises a domain
    error (ProductNotInCatalog / ProductUnavailable) the caller maps to a reply."""
    confirmed = ConfirmedOrder(**parsed.model_dump())
    customer_id = ctx.identity.actor.id
    order = await OrderService(ctx.session).create_order(
        tenant_id=ctx.tenant_id,
        customer_id=customer_id,
        confirmed=confirmed,
    )
    log.info(
        "tool.confirm_order",
        tenant_id=str(ctx.tenant_id),
        order_id=str(order.id),
        total_lbp=order.total_lbp,
    )
    return order


# Max policy/hours chunks fed to the answer step — a bill's worth of context is
# plenty for a single question; more just dilutes the prompt.
_KB_TOP_K = 4


async def search_knowledge_base(ctx: ToolContext, query: str) -> list[str]:
    """Retrieve the most relevant `knowledge` chunks for a customer question.

    Embeds the query and searches THIS tenant's knowledge corpus (products, policies,
    hours), tenant-filtered BEFORE similarity (the Wall — VectorChunkRepository.search).
    Read-only. Returns the chunk texts (possibly empty). Degrades to [] if there is no
    embedder or the embed call fails — a retrieval hiccup must never crash the reply.
    """
    if ctx.embedding_client is None:
        return []
    try:
        qvec = await ctx.embedding_client.embed_one(query)
    except Exception as e:  # noqa: BLE001 — degrade, never crash the customer reply
        log.warning("tool.search_kb.embed_error", tenant_id=str(ctx.tenant_id), error=str(e))
        return []
    hits = await VectorChunkRepository(ctx.session).search(
        ctx.tenant_id, corpus="knowledge", query_embedding=qvec, k=_KB_TOP_K
    )
    log.info("tool.search_kb", tenant_id=str(ctx.tenant_id), hits=len(hits))
    return [h.chunk_text for h in hits]


async def answer_from_knowledge(ctx: ToolContext, query: str, chunks: list[str]) -> str | None:
    """Compose a Lebanese-Arabic answer to `query` GROUNDED in the retrieved chunks.

    Returns None when there is nothing to ground on (no chunks) — the caller then
    falls back to "didn't understand" rather than letting the model invent an answer.
    The model is told to answer ONLY from the provided context and to say it doesn't
    know otherwise (no hallucinated policy). A provider error degrades to None.
    """
    if not chunks:
        return None
    context = "\n".join(f"- {c}" for c in chunks)
    system = order_agent_ar.KB_ANSWER_SYSTEM.format(context=context)
    messages = [SystemMessage(content=system), HumanMessage(content=query)]
    try:
        reply = await ctx.router.tier1().ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — degrade to None, never crash
        log.warning("tool.answer_kb.llm_error", tenant_id=str(ctx.tenant_id), error=str(e))
        return None
    text = (reply.content if hasattr(reply, "content") else str(reply)).strip()
    return text or None
