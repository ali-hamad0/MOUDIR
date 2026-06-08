"""FinanceAgent — a standalone LangGraph StateGraph for revenue analysis.

Flow:  gather_revenue → detect_anomalies → compose_reply

Read-only (no writes, no HIL gate). Constitution IV is enforced across the graph:
  - gather_revenue: reads daily revenue from Postgres (no LLM)
  - detect_anomalies: calls the ML AnomalyDetector (no LLM)
  - compose_reply: Tier 1 LLM explains the ML flags in Lebanese Arabic

Mirrors the InventoryAgent pattern: compiled once in lifespan, one session per
call, ToolContext injected via config so the shared graph stays concurrency-safe.
"""

from collections.abc import Sequence
from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.finance.schemas import AnomalyResult, RevenueSummary
from app.agents.finance.tools import ToolContext, compose_reply, flag_anomalies, get_revenue_summary
from app.agents.guardrails import check_output
from app.agents.llm.router import LLMRouter
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import AnomalyDetector, StubAnomalyDetector
from prompts import finance_agent_ar

log = get_logger(__name__)


class _FinanceState(TypedDict, total=False):
    question: str
    days: int
    revenue_rows: Sequence  # raw rows from daily_revenue; passed unchanged to the detector
    revenue_summary: RevenueSummary | None
    anomaly_result: AnomalyResult | None
    reply: str


def _ctx_of(config: RunnableConfig) -> ToolContext:
    return config["configurable"]["ctx"]


async def _gather_revenue(state: _FinanceState, config: RunnableConfig) -> _FinanceState:
    ctx = _ctx_of(config)
    rows, summary = await get_revenue_summary(ctx, days=state.get("days", 30))
    return {"revenue_rows": rows, "revenue_summary": summary}


async def _detect_anomalies(state: _FinanceState, config: RunnableConfig) -> _FinanceState:
    ctx = _ctx_of(config)
    result = flag_anomalies(ctx, state["revenue_rows"])
    return {"anomaly_result": result}


async def _compose_reply(state: _FinanceState, config: RunnableConfig) -> _FinanceState:
    ctx = _ctx_of(config)
    reply = await compose_reply(
        ctx,
        question=state.get("question", ""),
        summary=state["revenue_summary"],
        anomaly_result=state["anomaly_result"],
    )
    return {"reply": reply}


def _build_graph():
    graph: StateGraph = StateGraph(_FinanceState)
    graph.add_node("gather_revenue", _gather_revenue)
    graph.add_node("detect_anomalies", _detect_anomalies)
    graph.add_node("compose_reply", _compose_reply)
    graph.add_edge(START, "gather_revenue")
    graph.add_edge("gather_revenue", "detect_anomalies")
    graph.add_edge("detect_anomalies", "compose_reply")
    graph.add_edge("compose_reply", END)
    return graph.compile()


class FinanceAgent:
    """Revenue-analysis graph. Built once (lifespan); `handle` is called per owner question.

    Read-only — no writes, no HIL gate. The ML detector flags anomalous days;
    the LLM only explains them (Constitution IV).
    """

    def __init__(
        self,
        router: LLMRouter,
        settings: Settings,
        sessionmaker: async_sessionmaker,
        anomaly_detector: AnomalyDetector | None = None,
    ) -> None:
        self._router = router
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._anomaly_detector = anomaly_detector or StubAnomalyDetector()
        self._graph = _build_graph()

    async def handle(self, question: str, tenant_id: UUID, *, days: int = 30) -> str:
        """Handle an owner's revenue question. Returns a Lebanese Arabic reply.

        Opens its own session per call so the lifespan-built agent is concurrency-safe.
        The session is read-only; no commit is needed.
        """
        async with self._sessionmaker() as session:
            ctx = ToolContext(
                session=session,
                tenant_id=tenant_id,
                router=self._router,
                settings=self._settings,
                anomaly_detector=self._anomaly_detector,
            )
            log.info("finance_agent.handle", tenant_id=str(tenant_id), days=days)
            final: _FinanceState = await self._graph.ainvoke(
                {"question": question, "days": days},
                config={"configurable": {"ctx": ctx}},
            )
            reply = final.get("reply", finance_agent_ar.FALLBACK_REPLY)
            rail = check_output(reply, "finance")
            if not rail.allowed:
                log.warning(
                    "finance_agent.output_rail",
                    tenant_id=str(tenant_id),
                    reason=rail.reason,
                    matched=rail.matched,
                )
                return finance_agent_ar.FALLBACK_REPLY
            return rail.text or reply
