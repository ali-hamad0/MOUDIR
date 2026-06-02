"""OrderAgent tools: get_products, parse_order, confirm_order.

All three are tenant-scoped through a ToolContext — a tool cannot run outside a
tenant. I/O is Pydantic-validated; bad LLM output in parse_order triggers a retry
(up to settings.llm_max_retries), never a crash. confirm_order re-validates every
line against the live catalog (the final guard against a hallucinated product).
"""

from dataclasses import dataclass
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm.router import LLMRouter
from app.agents.order.schemas import ConfirmedOrder, ParsedOrder
from app.db.models import Order
from app.domain.identity import ResolvedIdentity
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.repositories.products import ProductRepository
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


async def parse_order(
    ctx: ToolContext, text: str, catalog: list[CatalogItem]
) -> ParsedOrder | None:
    """Extract a structured order from Lebanese-Arabic text, constrained to the
    catalog. Returns None when the message cannot be understood as an order or the
    LLM keeps producing invalid output after retries — the caller replies politely,
    it does not crash."""
    model = ctx.router.tier1().with_structured_output(ParsedOrder)
    system = order_agent_ar.PARSE_ORDER_SYSTEM.format(catalog=_render_catalog(catalog))
    messages = [SystemMessage(content=system), HumanMessage(content=text)]

    valid_ids = {item.id for item in catalog if item.is_available}
    attempts = ctx.settings.llm_max_retries + 1
    for attempt in range(attempts):
        try:
            parsed: ParsedOrder = await model.ainvoke(messages)
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

        # Drop any line whose product id is not an available catalog id — the LLM
        # does not get to introduce products even if it tried.
        parsed.items = [line for line in parsed.items if line.product_id in valid_ids]
        if not parsed.items:
            log.info("tool.parse_order.no_catalog_items", tenant_id=str(ctx.tenant_id))
            return None
        log.info(
            "tool.parse_order.ok",
            tenant_id=str(ctx.tenant_id),
            items=len(parsed.items),
            attempt=attempt + 1,
        )
        return parsed

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
