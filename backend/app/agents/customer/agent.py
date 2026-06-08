"""CustomerAgent — a standalone LangGraph StateGraph for churn risk + re-engagement.

Flow:  assess_risks → (no risks → compose_reply → END)
                    → draft_message → queue_draft → compose_reply → END

Constitution IV: ChurnPredictor ranks at-risk customers (no LLM in the risk step).
The LLM ONLY drafts the re-engagement message text.

Constitution V: queue_reengagement writes a draft to pending_reengagements (status
"draft"). The owner must approve from the dashboard — there is NO tool path to a
WhatsApp send in Phase 7. That is Phase 10 (Meta API).

Mirrors the InventoryAgent/FinanceAgent pattern: compiled once in lifespan, one
session per call, ToolContext injected via config for concurrency safety.
"""

from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.customer.schemas import ChurnRisks, QueuedAction
from app.agents.customer.tools import (
    ToolContext,
    _build_owner_reply,
    draft_reengagement,
    get_churn_risks,
    queue_reengagement,
)
from app.agents.llm.router import LLMRouter
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import ChurnPredictor, StubChurnPredictor
from prompts import customer_agent_ar

log = get_logger(__name__)


class _CustomerState(TypedDict, total=False):
    question: str
    churn_risks: ChurnRisks | None
    draft_message_ar: str
    queued_action: QueuedAction | None
    reply: str


def _ctx_of(config: RunnableConfig) -> ToolContext:
    return config["configurable"]["ctx"]


async def _assess_risks(state: _CustomerState, config: RunnableConfig) -> _CustomerState:
    ctx = _ctx_of(config)
    risks = await get_churn_risks(ctx)
    return {"churn_risks": risks}


async def _draft_message(state: _CustomerState, config: RunnableConfig) -> _CustomerState:
    ctx = _ctx_of(config)
    draft = await draft_reengagement(ctx, state["churn_risks"].customers[0])
    return {"draft_message_ar": draft}


async def _queue_draft(state: _CustomerState, config: RunnableConfig) -> _CustomerState:
    ctx = _ctx_of(config)
    action = await queue_reengagement(
        ctx, state["churn_risks"].customers[0], state["draft_message_ar"]
    )
    return {"queued_action": action}


async def _compose_reply(state: _CustomerState, config: RunnableConfig) -> _CustomerState:
    reply = _build_owner_reply(state.get("churn_risks"), state.get("queued_action"))
    return {"reply": reply}


def _route_after_assess(state: _CustomerState) -> str:
    risks = state.get("churn_risks")
    if risks and risks.customers:
        return "draft_message"
    return "compose_reply"


def _build_graph():
    graph: StateGraph = StateGraph(_CustomerState)
    graph.add_node("assess_risks", _assess_risks)
    graph.add_node("draft_message", _draft_message)
    graph.add_node("queue_draft", _queue_draft)
    graph.add_node("compose_reply", _compose_reply)
    graph.add_edge(START, "assess_risks")
    graph.add_conditional_edges(
        "assess_risks",
        _route_after_assess,
        {"draft_message": "draft_message", "compose_reply": "compose_reply"},
    )
    graph.add_edge("draft_message", "queue_draft")
    graph.add_edge("queue_draft", "compose_reply")
    graph.add_edge("compose_reply", END)
    return graph.compile()


class CustomerAgent:
    """Churn-risk + re-engagement graph. Built once (lifespan); `handle` per owner question.

    Constitution V: the graph has no path to a WhatsApp send. queue_reengagement
    writes a draft for HIL approval only. The send is Phase 10.
    """

    def __init__(
        self,
        router: LLMRouter,
        settings: Settings,
        sessionmaker: async_sessionmaker,
        churn_predictor: ChurnPredictor | None = None,
    ) -> None:
        self._router = router
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._churn_predictor = churn_predictor or StubChurnPredictor()
        self._graph = _build_graph()

    async def handle(self, question: str, tenant_id: UUID) -> str:
        """Handle an owner's customer/churn question. Returns a Lebanese Arabic reply.

        Opens its own session per call. Commits only if a re-engagement draft was
        queued (queue_reengagement flushes; the commit is deferred here so the write
        is a single unit of work under this session, not the tool's).
        """
        async with self._sessionmaker() as session:
            ctx = ToolContext(
                session=session,
                tenant_id=tenant_id,
                router=self._router,
                settings=self._settings,
                churn_predictor=self._churn_predictor,
            )
            log.info("customer_agent.handle", tenant_id=str(tenant_id))
            final: _CustomerState = await self._graph.ainvoke(
                {"question": question},
                config={"configurable": {"ctx": ctx}},
            )
            if final.get("queued_action") is not None:
                await session.commit()
            return final.get("reply", customer_agent_ar.FALLBACK_REPLY)
