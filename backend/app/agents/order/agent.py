"""OrderAgent — a LangGraph StateGraph wiring the three order tools.

Flow:  load_catalog → parse → (None → "didn't understand" reply)
                              → confirm → (domain error → "unavailable"/"not in
                                           catalog" reply)
                                        → success reply (Lebanese Arabic, LBP total)

It is a real graph (not a hand-rolled loop) so Phase 7 can drop it under the
supervisor as a sub-graph without a rewrite. The agent opens its own DB session
per handle() call from an injected sessionmaker — it runs outside the request.

The per-message ToolContext (which holds a live, tenant-scoped session) is passed
through the graph's per-invocation `config`, NOT stored on the instance — so the
single lifespan-built agent is safe under concurrent webhooks.
"""

from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.llm.router import LLMRouter
from app.agents.order.schemas import ParsedOrder
from app.agents.order.tools import (
    CatalogItem,
    ToolContext,
    confirm_order,
    get_products,
    parse_order,
)
from app.domain.errors import ProductNotInCatalog, ProductUnavailable
from app.domain.identity import ResolvedIdentity
from app.infra.logging import get_logger
from app.infra.settings import Settings
from prompts import order_ar

log = get_logger(__name__)


class _OrderState(TypedDict, total=False):
    text: str
    catalog: list[CatalogItem]
    parsed: ParsedOrder | None
    reply: str


def _ctx_of(config: RunnableConfig) -> ToolContext:
    return config["configurable"]["ctx"]


def _render_catalog_names(catalog: list[CatalogItem]) -> str:
    return "، ".join(item.name_ar for item in catalog if item.is_available)


def _success_reply(total_lbp: int | None, fulfillment_type: str) -> str:
    fulfillment = (
        order_ar.FULFILLMENT_DELIVERY
        if fulfillment_type == "delivery"
        else order_ar.FULFILLMENT_PICKUP
    )
    return order_ar.ORDER_CONFIRMED.format(total_lbp=total_lbp or 0, fulfillment=fulfillment)


# ---- Graph nodes (module-level; per-message context comes via config) ----
async def _load_catalog(state: _OrderState, config: RunnableConfig) -> _OrderState:
    return {"catalog": await get_products(_ctx_of(config))}


async def _parse(state: _OrderState, config: RunnableConfig) -> _OrderState:
    parsed = await parse_order(_ctx_of(config), state["text"], state["catalog"])
    if parsed is None:
        return {"parsed": None, "reply": order_ar.DID_NOT_UNDERSTAND}
    return {"parsed": parsed}


async def _confirm(state: _OrderState, config: RunnableConfig) -> _OrderState:
    try:
        order = await confirm_order(_ctx_of(config), state["parsed"])
    except ProductUnavailable:
        return {"reply": order_ar.PRODUCT_NOT_AVAILABLE}
    except ProductNotInCatalog:
        items = _render_catalog_names(state["catalog"])
        return {"reply": order_ar.PRODUCT_NOT_IN_CATALOG.format(items=items)}
    return {"reply": _success_reply(order.total_lbp, order.fulfillment_type)}


def _build_graph():
    graph: StateGraph = StateGraph(_OrderState)
    graph.add_node("load_catalog", _load_catalog)
    graph.add_node("parse", _parse)
    graph.add_node("confirm", _confirm)

    graph.add_edge(START, "load_catalog")
    graph.add_edge("load_catalog", "parse")
    # A None parse result short-circuits to END with the reply already set.
    graph.add_conditional_edges(
        "parse",
        lambda state: "confirm" if state.get("parsed") is not None else END,
        {"confirm": "confirm", END: END},
    )
    graph.add_edge("confirm", END)
    return graph.compile()


class OrderAgent:
    """Customer order graph. Built once (lifespan); `handle` is called per message."""

    def __init__(
        self,
        router: LLMRouter,
        settings: Settings,
        sessionmaker: async_sessionmaker,
    ) -> None:
        self._router = router
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._graph = _build_graph()

    async def handle(self, text: str, identity: ResolvedIdentity) -> str:
        """Run the graph for one inbound customer message and return the reply."""
        async with self._sessionmaker() as session:
            ctx = ToolContext(
                session=session,
                identity=identity,
                router=self._router,
                settings=self._settings,
            )
            log.info(
                "order_agent.handle",
                tenant_id=str(identity.tenant.id),
                role=identity.role,
            )
            final: _OrderState = await self._graph.ainvoke(
                {"text": text}, config={"configurable": {"ctx": ctx}}
            )
            return final.get("reply", order_ar.DID_NOT_UNDERSTAND)
