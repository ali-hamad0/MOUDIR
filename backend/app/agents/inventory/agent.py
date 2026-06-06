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

from app.agents.inventory.tools import (
    ToolContext,
    check_stock,
    draft_purchase_order,
    forecast_demand,
)
from app.agents.llm.router import LLMRouter
from app.db.models import Inventory
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import DemandPredictor, StubDemandPredictor
from app.repositories.products import ProductRepository
from app.repositories.training_data import TrainingDataRepository

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
