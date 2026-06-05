"""BillExtractionAgent tools: extract_bill, map_lines_to_products.

The agent STRUCTURES OCR text into a BillData (constitution IV: the LLM works on the
text, never the image) and best-effort maps each extracted line to a product in the
tenant's catalog. Both are tenant-scoped through a ToolContext — a tool cannot run
outside a tenant (the Wall holds at the tool boundary too).

The extraction step is Pydantic-validated; bad LLM output retries up to
settings.llm_max_retries, then degrades to an empty extraction (the worker records
`ocr_failed`) — it never crashes. Mapping never creates catalog rows; an unmatched
line stays unmapped for the human to map in review (Task 5.12).
"""

from dataclasses import dataclass
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm.router import LLMRouter
from app.agents.ocr.schemas import BillData
from app.db.models import Product
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.repositories.products import ProductRepository
from prompts import bill_agent_ar

log = get_logger(__name__)


@dataclass
class ToolContext:
    """Everything a bill tool needs, bound to one tenant. Like the InventoryAgent,
    the OCR agent acts on the SYSTEM's behalf (a reaction to an uploaded bill), so
    there is no customer/owner WhatsApp identity — just the tenant scope plus the
    session/router/settings. Passing this (not a bare session) keeps every tool
    inside the Wall."""

    session: AsyncSession
    tenant_id: UUID
    router: LLMRouter
    settings: Settings


async def extract_bill(ctx: ToolContext, ocr_text: str) -> BillData:
    """Structure OCR text into a BillData via the Tier-1 model. NEVER reads pixels.

    Output is Pydantic-validated (BillData); a ValidationError or a provider error
    retries up to settings.llm_max_retries, then degrades to an EMPTY BillData (the
    worker maps that to `ocr_failed`) — a flaky LLM must never crash the pipeline.
    Tier 1 (Flash) is enough for structured extraction; Pro is reserved for harder
    work (ROADMAP tier rule).
    """
    model = ctx.router.tier1().with_structured_output(BillData)
    messages = [
        SystemMessage(content=bill_agent_ar.EXTRACT_SYSTEM),
        HumanMessage(content=bill_agent_ar.EXTRACT_HUMAN.format(ocr_text=ocr_text)),
    ]

    attempts = ctx.settings.llm_max_retries + 1
    for attempt in range(attempts):
        try:
            data: BillData = await model.ainvoke(messages)
        except ValidationError as e:
            log.warning(
                "tool.extract_bill.invalid",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error=str(e),
            )
            continue
        except Exception as e:
            # Provider/transport failure — stop retrying, degrade gracefully.
            log.warning(
                "tool.extract_bill.llm_error",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error_type=type(e).__name__,
            )
            break
        log.info(
            "tool.extract_bill.ok",
            tenant_id=str(ctx.tenant_id),
            attempt=attempt + 1,
            lines=len(data.lines),
        )
        return data

    log.info("tool.extract_bill.degraded", tenant_id=str(ctx.tenant_id))
    return BillData(certainty=0.0)


def _normalize(name: str) -> str:
    """Lowercase + collapse whitespace for a forgiving name comparison. (Best-effort
    Arabic matching; a fuller normalization/fuzzy match is a later refinement.)"""
    return " ".join(name.split()).strip().casefold()


async def map_lines_to_products(ctx: ToolContext, data: BillData) -> list[UUID | None]:
    """Best-effort map each extracted line to a catalog product, by name.

    Returns a product_id (or None) per line, in order. Exact normalized match first,
    then a containment match (the bill name contains, or is contained by, a product
    name). NEVER creates a catalog row — an unmatched line stays None for the human
    to map in review (Task 5.12). Tenant-scoped: only THIS tenant's catalog is
    considered (the Wall).
    """
    products: list[Product] = list(await ProductRepository(ctx.session).list(ctx.tenant_id))
    by_name: dict[str, UUID] = {_normalize(p.name_ar): p.id for p in products if p.name_ar}

    mapped: list[UUID | None] = []
    for line in data.lines:
        if not line.name_ar:
            mapped.append(None)
            continue
        key = _normalize(line.name_ar)
        product_id = by_name.get(key)
        if product_id is None:
            # Containment fallback: "طحين أبيض" on the bill ↔ "طحين" in the catalog.
            for pname, pid in by_name.items():
                if pname and (pname in key or key in pname):
                    product_id = pid
                    break
        mapped.append(product_id)

    matched = sum(1 for m in mapped if m is not None)
    log.info(
        "tool.map_lines_to_products",
        tenant_id=str(ctx.tenant_id),
        lines=len(data.lines),
        matched=matched,
    )
    return mapped
