"""InventoryAgent — a standalone LangGraph StateGraph that drafts reorder POs.

Flow:  check_stock → (no inventory row → END, nothing to reorder)
                    → forecast_demand (trained demand model, Phase 6) → draft_purchase_order
                      (Tier-1 Arabic note; writes a `draft` PO; NEVER sends)

It mirrors the Phase 2 OrderAgent EXACTLY so Phase 7 can drop it under the
supervisor without a rewrite: a compiled graph built ONCE (in lifespan), opening
its own DB session per call from an injected sessionmaker, with the per-call
ToolContext passed through the graph's `config` — never stored on the instance, so
the single lifespan-built agent is concurrency-safe.

The agent only DRAFTS. Every send is governed by the human gate (4.10/4.12); a
draft sits in the approvals inbox until a human approves it.
"""

from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.guardrails import check_output
from app.agents.inventory.tools import (
    ToolContext,
    check_stock,
    draft_purchase_order,
    forecast_demand,
    parse_adjustment,
)
from app.agents.llm.router import LLMRouter

# The order flow's code-side Arabic matcher, reused so the same phrase matches the
# same product whether a customer orders it or the owner restocks it. The LLM never
# picks a product id in either flow.
from app.agents.order.tools import CatalogItem, _match_phrase_to_catalog
from app.db.models import Inventory
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import DemandPredictor, StubDemandPredictor
from app.repositories.inventory import InventoryRepository
from app.repositories.products import ProductRepository
from app.repositories.training_data import TrainingDataRepository
from app.services.audit import AuditService
from prompts import inventory_ar

log = get_logger(__name__)


class _InventoryState(TypedDict, total=False):
    product_id: UUID
    inventory: Inventory | None
    suggested_qty: int
    po_id: UUID | None


def _ctx_of(config: RunnableConfig) -> ToolContext:
    return config["configurable"]["ctx"]


# ---- Graph nodes (module-level; per-call context comes via config) ----
async def _check_stock(state: _InventoryState, config: RunnableConfig) -> _InventoryState:
    row = await check_stock(_ctx_of(config), state["product_id"])
    return {"inventory": row}


async def _forecast(state: _InventoryState, config: RunnableConfig) -> _InventoryState:
    return {"suggested_qty": forecast_demand(_ctx_of(config), state["inventory"])}


async def _draft(state: _InventoryState, config: RunnableConfig) -> _InventoryState:
    ctx = _ctx_of(config)
    inventory = state["inventory"]
    # Load the product name for the note (tenant-scoped). If the product vanished,
    # fall back to a neutral label rather than failing the draft.
    product = await ProductRepository(ctx.session).get(ctx.tenant_id, state["product_id"])
    product_name = product.name_ar if product is not None else "منتج"
    po = await draft_purchase_order(
        ctx,
        product_id=state["product_id"],
        product_name=product_name,
        supplier_id=inventory.supplier_id,
        suggested_qty=state["suggested_qty"],
        reason="crossed reorder threshold",
    )
    return {"po_id": po.id}


def _build_graph():
    graph: StateGraph = StateGraph(_InventoryState)
    graph.add_node("check_stock", _check_stock)
    graph.add_node("forecast", _forecast)
    graph.add_node("draft", _draft)

    graph.add_edge(START, "check_stock")
    # No inventory row → nothing to reorder; short-circuit to END.
    graph.add_conditional_edges(
        "check_stock",
        lambda state: "forecast" if state.get("inventory") is not None else END,
        {"forecast": "forecast", END: END},
    )
    graph.add_edge("forecast", "draft")
    graph.add_edge("draft", END)
    return graph.compile()


class InventoryAgent:
    """Reorder-drafting graph. Built once (lifespan); `draft_for_low_stock` is
    called per low-stock product (inline from order completion, Task 4.9)."""

    def __init__(
        self,
        router: LLMRouter,
        settings: Settings,
        sessionmaker: async_sessionmaker,
        demand_predictor: DemandPredictor | None = None,
    ) -> None:
        self._router = router
        self._settings = settings
        self._sessionmaker = sessionmaker
        # The lifespan injects the trained (or stub) predictor; default to the offline
        # stub so existing callers/tests that build the agent with three args still work
        # and stay offline (the stub returns None → forecast_demand's documented fallback).
        self._demand_predictor = demand_predictor or StubDemandPredictor()
        self._graph = _build_graph()

    async def draft_for_low_stock(self, tenant_id: UUID, product_id: UUID) -> UUID | None:
        """Draft a reorder PO for one low-stock product. Returns the new PO id, or
        None if there was nothing to reorder (no inventory row).

        Opens its own session (it runs outside any request — inline after order
        completion commits, 4.9). The per-call ToolContext is passed via config, so
        the shared graph stays concurrency-safe. The session is committed here: the
        draft is its own unit of work, decoupled from the completion transaction
        that triggered it (a draft hiccup must never roll back a real fulfillment).
        """
        async with self._sessionmaker() as session:
            # Pre-fetch this product's daily-demand series (tenant-scoped) so the sync
            # forecast_demand can feed it to the predictor without awaiting (AD-6.5). For
            # a brand-new product with no orders this is empty → the predictor returns
            # None → forecast_demand uses its documented fallback.
            demand_history = await TrainingDataRepository(session).daily_product_demand(
                tenant_id, product_id=product_id
            )
            ctx = ToolContext(
                session=session,
                tenant_id=tenant_id,
                router=self._router,
                settings=self._settings,
                demand_predictor=self._demand_predictor,
                demand_history=demand_history,
            )
            log.info(
                "inventory_agent.draft_for_low_stock",
                tenant_id=str(tenant_id),
                product_id=str(product_id),
            )
            final: _InventoryState = await self._graph.ainvoke(
                {"product_id": product_id}, config={"configurable": {"ctx": ctx}}
            )
            po_id = final.get("po_id")
            if po_id is not None:
                await session.commit()
            return po_id

    async def propose_adjustment(
        self, message: str, tenant_id: UUID
    ) -> tuple[str, dict | None] | None:
        """Parse an owner stock-edit request and build the confirmation step.

        Returns None when the message is not a stock edit (the caller falls back
        to the read-only status path). Otherwise returns (reply, pending):
        - a parsed + matched edit → the «نعم/لا» confirmation question and a
          JSON-safe pending dict the supervisor keeps in checkpointed state;
        - an unmatched product or an impossible subtract → an error reply with
          pending=None (nothing to confirm, nothing written).

        Nothing is written here — the write happens only in apply_adjustment,
        after the owner confirms (the same human-gate discipline as the PO flow).
        """
        async with self._sessionmaker() as session:
            ctx = ToolContext(
                session=session,
                tenant_id=tenant_id,
                router=self._router,
                settings=self._settings,
            )
            raw = await parse_adjustment(ctx, message)
            if raw is None:
                return None

            products = await ProductRepository(session).list(tenant_id)
            catalog = [CatalogItem(id=p.id, name_ar=p.name_ar, is_available=True) for p in products]
            match = _match_phrase_to_catalog(raw.product_phrase, catalog)
            if match is None:
                log.info(
                    "inventory_agent.adjust.unmatched",
                    tenant_id=str(tenant_id),
                )
                reply = inventory_ar.ADJUST_PRODUCT_NOT_FOUND.format(phrase=raw.product_phrase)
                return reply, None

            row = await InventoryRepository(session).get_by_product(tenant_id, match.id)
            old = row.quantity if row is not None else 0

        if raw.action == "add":
            new = old + raw.quantity
        elif raw.action == "subtract":
            if raw.quantity > old:
                reply = inventory_ar.ADJUST_INSUFFICIENT.format(
                    amount=raw.quantity, product_name=match.name_ar, old=old
                )
                return reply, None
            new = old - raw.quantity
        else:  # set
            new = raw.quantity

        pending = {
            # All primitives — this dict lives in the supervisor's checkpointed
            # state (Postgres JSON), so no UUID/ORM objects.
            "product_id": str(match.id),
            "product_name": match.name_ar,
            "action": raw.action,
            "amount": raw.quantity,
            "old_quantity": old,
            "new_quantity": new,
        }
        log.info(
            "inventory_agent.adjust.proposed",
            tenant_id=str(tenant_id),
            product_id=str(match.id),
            action=raw.action,
            amount=raw.quantity,
        )
        reply = inventory_ar.ADJUST_CONFIRM.format(product_name=match.name_ar, old=old, new=new)
        rail = check_output(reply, "inventory")
        return (rail.text or reply), pending

    async def apply_adjustment(self, tenant_id: UUID, pending: dict) -> str:
        """Apply a confirmed stock edit, atomically, then audit and commit.

        `pending` is the dict propose_adjustment built. add/subtract apply as
        atomic deltas against the CURRENT quantity (stock may have moved between
        propose and confirm — e.g. an order deducted units); `set` is absolute.
        A subtract that no longer fits returns the insufficient reply and writes
        nothing. Audited as `inventory.adjusted` — the same action the dashboard
        edit writes (actor_id is None: the supervisor is actor-blind by design;
        the owner's phone identity is already in the webhook log line).
        """
        product_id = UUID(pending["product_id"])
        action: str = pending["action"]
        amount: int = pending["amount"]
        async with self._sessionmaker() as session:
            repo = InventoryRepository(session)
            await repo.ensure_row(tenant_id, product_id)
            if action == "add":
                ok = await repo.increase(tenant_id, product_id, amount)
            elif action == "subtract":
                ok = await repo.deduct(tenant_id, product_id, amount)
            else:  # set
                ok = await repo.set_quantity(tenant_id, product_id, amount)

            if not ok:
                # Only the guarded deduct can miss: stock moved below the amount
                # between propose and confirm. Nothing was written.
                await session.rollback()
                row = await repo.get_by_product(tenant_id, product_id)
                old = row.quantity if row is not None else 0
                log.info(
                    "inventory_agent.adjust.insufficient",
                    tenant_id=str(tenant_id),
                    product_id=str(product_id),
                )
                return inventory_ar.ADJUST_INSUFFICIENT.format(
                    amount=amount, product_name=pending["product_name"], old=old
                )

            row = await repo.get_by_product(tenant_id, product_id)
            new_quantity = row.quantity if row is not None else amount
            await AuditService(session).record(
                tenant_id=tenant_id,
                actor_id=None,
                action="inventory.adjusted",
                target=f"{product_id} whatsapp {action} {amount}",
            )
            await session.commit()
        log.info(
            "inventory_agent.adjust.applied",
            tenant_id=str(tenant_id),
            product_id=str(product_id),
            action=action,
            amount=amount,
            new_quantity=new_quantity,
        )
        reply = inventory_ar.ADJUST_APPLIED.format(
            product_name=pending["product_name"], new=new_quantity
        )
        rail = check_output(reply, "inventory")
        return rail.text or reply

    async def handle(self, question: str, tenant_id: UUID) -> str:
        """Answer an owner's inventory question. Returns Lebanese Arabic summary.

        Read-only: reports current low-stock status. The owner sees which products
        need attention; the actual PO draft is triggered automatically by order
        completion (draft_for_low_stock) and then awaits HIL approval in the
        dashboard — this method does not draft, approve, or send anything.
        """
        async with self._sessionmaker() as session:
            low_stock = await InventoryRepository(session).list_low_stock(tenant_id)
            if not low_stock:
                log.info("inventory_agent.handle.no_low_stock", tenant_id=str(tenant_id))
                return "مخزونك تمام! ما في منتجات تحتاج إعادة طلب هلق. 👍"
            product_repo = ProductRepository(session)
            lines = [f"🔴 عندك {len(low_stock)} منتج قريب من النفاد:"]
            for inv in low_stock:
                product = await product_repo.get(tenant_id, inv.product_id)
                name = product.name_ar if product is not None else "منتج"
                lines.append(f"  - {name}: {inv.quantity} وحدة متبقية")
            log.info(
                "inventory_agent.handle.low_stock",
                tenant_id=str(tenant_id),
                count=len(low_stock),
            )
            reply = "\n".join(lines)
            rail = check_output(reply, "inventory")
            return rail.text or reply
