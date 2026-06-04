"""InventoryAgent tools: check_stock, forecast_demand, draft_purchase_order.

All three are tenant-scoped through a ToolContext — a tool cannot run outside a
tenant (the Wall holds at the tool boundary too). Only draft_purchase_order uses
the LLM, and only for a short Arabic supplier note: its output is Pydantic-
validated and a bad result retries (then falls back to a templated note), never
crashes. The PO it writes is a `draft` — it NEVER sends (the human gate, 4.10/4.12,
governs every send).
"""

from dataclasses import dataclass
from uuid import UUID

from langchain_core.messages import SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.inventory.schemas import SupplierNote
from app.agents.llm.router import LLMRouter
from app.db.models import Inventory, PurchaseOrder
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.repositories.inventory import InventoryRepository
from app.services.purchase_orders import PurchaseOrderService
from prompts import inventory_agent_ar

log = get_logger(__name__)

# The deterministic fallback reorder quantity when the inventory row has no
# `reorder_quantity` set. A documented fixed default — NOT ML. Phase 6 replaces
# forecast_demand with a trained model behind this same signature; no caller
# changes. (Constitution IV: ML is Phase 6; a placeholder is explicitly allowed
# here per the phase plan.)
DEFAULT_REORDER_QTY = 10


@dataclass
class ToolContext:
    """Everything an inventory tool needs, bound to one tenant. Unlike the order
    flow there is no customer/owner WhatsApp identity here — the InventoryAgent
    acts on the SYSTEM's behalf (a reaction to stock dropping), so the context is
    just the tenant scope plus the session/router/settings. Passing this (not a
    bare session) keeps every tool inside the Wall."""

    session: AsyncSession
    tenant_id: UUID
    router: LLMRouter
    settings: Settings


async def check_stock(ctx: ToolContext, product_id: UUID) -> Inventory | None:
    """Read the current inventory row for a product, tenant-scoped. No LLM.

    Returns None if the product is untracked for this tenant (no inventory row) —
    the caller decides what that means (the completion flow skips untracked lines;
    the draft flow would have nothing to reorder)."""
    row = await InventoryRepository(ctx.session).get_by_product(ctx.tenant_id, product_id)
    log.info(
        "tool.check_stock",
        tenant_id=str(ctx.tenant_id),
        product_id=str(product_id),
        quantity=row.quantity if row is not None else None,
    )
    return row


def forecast_demand(ctx: ToolContext, inventory: Inventory) -> int:
    """Suggest a reorder quantity. **PLACEHOLDER — no ML (constitution IV).**

    Phase 6 replaces this with a trained demand model behind this exact signature,
    so no caller changes. For now it is deterministic: use the owner's configured
    `reorder_quantity` if set, else a documented fixed default. Always returns a
    positive integer (a draft with quantity 0 would be meaningless).
    """
    qty = inventory.reorder_quantity if inventory.reorder_quantity else DEFAULT_REORDER_QTY
    suggested = max(1, qty)
    log.info(
        "tool.forecast_demand",
        tenant_id=str(ctx.tenant_id),
        product_id=str(inventory.product_id),
        suggested=suggested,
        source="reorder_quantity" if inventory.reorder_quantity else "default",
    )
    return suggested


async def _draft_note(ctx: ToolContext, product_name: str, quantity: int) -> str:
    """Compose a short Lebanese-Arabic supplier note via the Tier-1 model.

    Output is Pydantic-validated (SupplierNote); bad output or a provider error
    retries up to settings.llm_max_retries, then falls back to a templated note.
    A draft must always be producible — a flaky LLM must never block the reorder
    loop (so this returns a string rather than raising)."""
    model = ctx.router.tier1().with_structured_output(SupplierNote)
    system = inventory_agent_ar.DRAFT_NOTE_SYSTEM.format(
        product_name=product_name, quantity=quantity
    )
    messages = [SystemMessage(content=system)]

    attempts = ctx.settings.llm_max_retries + 1
    for attempt in range(attempts):
        try:
            note: SupplierNote = await model.ainvoke(messages)
        except (ValidationError, ValueError) as e:
            log.warning(
                "tool.draft_note.invalid",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error=str(e),
            )
            continue
        except Exception as e:
            # Provider/transport failure — degrade to the fallback, never crash.
            log.warning(
                "tool.draft_note.llm_error",
                tenant_id=str(ctx.tenant_id),
                attempt=attempt + 1,
                error_type=type(e).__name__,
            )
            break
        log.info("tool.draft_note.ok", tenant_id=str(ctx.tenant_id), attempt=attempt + 1)
        return note.note_ar

    log.info("tool.draft_note.fallback", tenant_id=str(ctx.tenant_id))
    return inventory_agent_ar.FALLBACK_NOTE.format(product_name=product_name, quantity=quantity)


async def draft_purchase_order(
    ctx: ToolContext,
    *,
    product_id: UUID,
    product_name: str,
    supplier_id: UUID | None,
    suggested_qty: int,
    reason: str | None = None,
) -> PurchaseOrder:
    """Draft a reorder PO with a Tier-1 Arabic supplier note. **NEVER sends.**

    Composes the note, then writes the PO via PurchaseOrderService.draft (the only
    PO-state writer) as status `draft` — it lands in the approvals inbox, awaiting a
    human. The human gate (4.10/4.12) is the only path to a send; nothing here
    dispatches.
    """
    note_ar = await _draft_note(ctx, product_name, suggested_qty)
    po = await PurchaseOrderService(ctx.session).draft(
        tenant_id=ctx.tenant_id,
        product_id=product_id,
        supplier_id=supplier_id,
        quantity=suggested_qty,
        reason=reason,
        agent_note_ar=note_ar,
    )
    log.info(
        "tool.draft_purchase_order",
        tenant_id=str(ctx.tenant_id),
        po_id=str(po.id),
        product_id=str(product_id),
        quantity=suggested_qty,
    )
    return po
