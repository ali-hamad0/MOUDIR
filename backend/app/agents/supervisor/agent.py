"""OwnerSupervisor — the Phase 7 LangGraph supervisor (Task 7.6).

Routes every owner WhatsApp message to the right specialist agent and persists
the checkpoint to Postgres (AD-7.2) so a mid-flight run survives a container
restart.

Architecture (AD-7.3):
  START → route → [conditional on intent] → one of 5 agent nodes → respond → END

The route node calls classify_intent (Tier-1 LLM) and sets state["intent"].
Each agent node calls the specialist's handle() — those agents own their own
sessions and ToolContexts; the supervisor just orchestrates.
The respond node logs the routing decision; Task 7.9 will add cost-tracking here.

The Wall in this context:
- state["tenant_id"] is a string UUID — JSON-serializable for Postgres checkpoints.
- thread_id = make_thread_id(tenant_id, session_id) — the ONLY place thread IDs
  are formed (app.infra.checkpointer) — enforces tenant isolation in LangGraph state.
- Agent calls receive UUID(state["tenant_id"]) — never a different tenant's UUID.

Order intent note (Phase 7): owner messages classified as "order" (e.g. "كم طلب
وصل اليوم؟") route to the AdvisorAgent, which already reports today's order count
as part of the daily snapshot. The OrderAgent (passed in for completeness) handles
the customer order PLACEMENT flow via the dispatcher's customer path — it is not
wired to the owner supervisor in Phase 7.
"""

from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.advisor.agent import AdvisorAgent
from app.agents.customer.agent import CustomerAgent
from app.agents.finance.agent import FinanceAgent
from app.agents.inventory.agent import InventoryAgent
from app.agents.llm.cost_callback import CostTrackingCallback
from app.agents.llm.router import LLMRouter
from app.agents.supervisor.routing import classify_intent
from app.agents.supervisor.state import SupervisorState
from app.infra.checkpointer import make_thread_id
from app.infra.logging import get_logger
from prompts import supervisor_ar

log = get_logger(__name__)

_INTENT_NODES = ("order", "inventory", "finance", "customer", "advisor")


class OwnerSupervisor:
    """Owner message router. Built once (lifespan); `handle` per owner message.

    The compiled graph is attached to the Postgres checkpointer so conversation
    state survives container restarts (AD-7.2). Thread IDs are always tenant-
    prefixed (make_thread_id) — the Wall holds in LangGraph state.
    """

    def __init__(
        self,
        router: LLMRouter,
        order_agent,  # OrderAgent — reserved for Phase 10 owner order management
        inventory_agent: InventoryAgent,
        finance_agent: FinanceAgent,
        customer_agent: CustomerAgent,
        advisor_agent: AdvisorAgent,
        checkpointer=None,  # AsyncPostgresSaver; None disables checkpointing (tests)
        sessionmaker: async_sessionmaker | None = None,  # for CostTrackingCallback
    ) -> None:
        self._router = router
        self._order_agent = order_agent
        self._inventory_agent = inventory_agent
        self._finance_agent = finance_agent
        self._customer_agent = customer_agent
        self._advisor_agent = advisor_agent
        self._checkpointer = checkpointer
        self._sessionmaker = sessionmaker
        self._graph = self._build_graph()

    # ── graph construction ─────────────────────────────────────────────────

    def _build_graph(self):
        graph: StateGraph = StateGraph(SupervisorState)

        # Nodes close over self so the compiled graph stays concurrency-safe:
        # each call supplies its own tenant_id through the state — no shared
        # mutable state lives on the supervisor instance.

        async def _route(state: SupervisorState, config: RunnableConfig) -> SupervisorState:
            intent = await classify_intent(state["message"], self._router.tier1())
            log.info(
                "supervisor.routed",
                tenant_id=state.get("tenant_id"),
                intent=intent,
            )
            return {"intent": intent, "routed_to": intent}

        async def _order_node(state: SupervisorState, config: RunnableConfig) -> SupervisorState:
            # Phase 7: owner "order" queries (e.g. "كم طلب وصل؟") → advisor
            # The advisor's snapshot includes today's order count. Phase 10 will
            # wire the OrderAgent here once the owner-facing order-management
            # interface is built.
            reply = await self._advisor_agent.handle(state["message"], UUID(state["tenant_id"]))
            return {"response": reply}

        async def _inventory_node(
            state: SupervisorState, config: RunnableConfig
        ) -> SupervisorState:
            reply = await self._inventory_agent.handle(state["message"], UUID(state["tenant_id"]))
            return {"response": reply}

        async def _finance_node(state: SupervisorState, config: RunnableConfig) -> SupervisorState:
            reply = await self._finance_agent.handle(state["message"], UUID(state["tenant_id"]))
            return {"response": reply}

        async def _customer_node(state: SupervisorState, config: RunnableConfig) -> SupervisorState:
            reply = await self._customer_agent.handle(state["message"], UUID(state["tenant_id"]))
            return {"response": reply}

        async def _advisor_node(state: SupervisorState, config: RunnableConfig) -> SupervisorState:
            reply = await self._advisor_agent.handle(state["message"], UUID(state["tenant_id"]))
            return {"response": reply}

        async def _respond(state: SupervisorState, config: RunnableConfig) -> SupervisorState:
            log.info(
                "supervisor.respond",
                tenant_id=state.get("tenant_id"),
                routed_to=state.get("routed_to"),
                response_len=len(state.get("response", "")),
            )
            return {}  # no state change; Task 7.9 will write cost row here

        graph.add_node("route", _route)
        graph.add_node("order", _order_node)
        graph.add_node("inventory", _inventory_node)
        graph.add_node("finance", _finance_node)
        graph.add_node("customer", _customer_node)
        graph.add_node("advisor", _advisor_node)
        graph.add_node("respond", _respond)

        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route",
            lambda state: state.get("intent", "advisor"),
            {name: name for name in _INTENT_NODES},
        )
        for name in _INTENT_NODES:
            graph.add_edge(name, "respond")
        graph.add_edge("respond", END)

        return graph.compile(checkpointer=self._checkpointer)

    # ── public API ─────────────────────────────────────────────────────────

    async def handle(self, message: str, tenant_id: UUID, session_id: str) -> str:
        """Route one owner message to the right specialist and return the reply.

        The thread_id is constructed via make_thread_id — the only place that
        function is called for supervisor threads (constitution I: never inline
        the prefix format). The Postgres checkpointer persists state under this
        thread_id so a restart with the same session_id resumes from the last
        checkpoint.

        Returns a Lebanese Arabic string. Returns the fallback message on any
        unrecoverable error (never raises to the caller).
        """
        thread_id = make_thread_id(tenant_id, session_id)
        callbacks = (
            [CostTrackingCallback(self._sessionmaker, tenant_id, "supervisor")]
            if self._sessionmaker is not None
            else []
        )
        config = {"configurable": {"thread_id": thread_id}, "callbacks": callbacks}
        try:
            final: SupervisorState = await self._graph.ainvoke(
                {
                    "message": message,
                    "tenant_id": str(tenant_id),
                    "session_id": session_id,
                },
                config=config,
            )
            log.info(
                "supervisor.handle.ok",
                tenant_id=str(tenant_id),
                session_id=session_id,
                routed_to=final.get("routed_to"),
            )
            self._router.mark_healthy()
            return final.get("response", supervisor_ar.FALLBACK_REPLY)
        except Exception as exc:
            log.error(
                "supervisor.handle.error",
                tenant_id=str(tenant_id),
                session_id=session_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self._router.mark_unhealthy()
            return supervisor_ar.FALLBACK_REPLY
