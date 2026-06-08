"""AdvisorAgent — morning briefing via LangGraph StateGraph (Task 7.5).

Flow: gather_snapshot → gather_demand → gather_churn → gather_anomaly → compose_briefing → END

Constitution IV (rigorously): the first four nodes call Phase 6 ML models or live DB.
The LLM is ONLY in the final compose_briefing node — it synthesizes structured inputs,
never produces numbers. The graph structure enforces this: no LLM call appears before
compose_briefing.

Read-only agent: no HIL queue, no commit. The AdvisorAgent writes nothing to the DB.
"""

from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.advisor.schemas import (
    AnomalyStatus,
    ChurnSummary,
    DemandForecast,
    TodaySnapshot,
)
from app.agents.advisor.tools import (
    ToolContext,
    compose_briefing,
    get_anomaly_status,
    get_churn_summary,
    get_demand_forecast,
    get_todays_snapshot,
)
from app.agents.llm.router import LLMRouter
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.ml.predictors import (
    AnomalyDetector,
    ChurnPredictor,
    DemandPredictor,
    StubAnomalyDetector,
    StubChurnPredictor,
    StubDemandPredictor,
)
from prompts import advisor_agent_ar

log = get_logger(__name__)

_EMPTY_SNAPSHOT = TodaySnapshot(
    today_revenue_lbp=0,
    avg_7day_lbp=0,
    same_day_last_week_lbp=0,
    order_count=0,
    top_products=[],
)
_EMPTY_DEMAND = DemandForecast(items=[])
_EMPTY_CHURN = ChurnSummary(count_at_risk=0, total_customers=0, top_names=[])
_EMPTY_ANOMALY = AnomalyStatus(is_today_anomalous=False, recent_flag_count=0, recent_flags=[])


class _AdvisorState(TypedDict, total=False):
    question: str
    snapshot: TodaySnapshot | None
    demand: DemandForecast | None
    churn: ChurnSummary | None
    anomaly: AnomalyStatus | None
    reply: str


def _ctx_of(config: RunnableConfig) -> ToolContext:
    return config["configurable"]["ctx"]


async def _gather_snapshot(state: _AdvisorState, config: RunnableConfig) -> _AdvisorState:
    ctx = _ctx_of(config)
    snap = await get_todays_snapshot(ctx)
    return {"snapshot": snap}


async def _gather_demand(state: _AdvisorState, config: RunnableConfig) -> _AdvisorState:
    ctx = _ctx_of(config)
    forecast = await get_demand_forecast(ctx)
    return {"demand": forecast}


async def _gather_churn(state: _AdvisorState, config: RunnableConfig) -> _AdvisorState:
    ctx = _ctx_of(config)
    churn = await get_churn_summary(ctx)
    return {"churn": churn}


async def _gather_anomaly(state: _AdvisorState, config: RunnableConfig) -> _AdvisorState:
    ctx = _ctx_of(config)
    anomaly = await get_anomaly_status(ctx)
    return {"anomaly": anomaly}


async def _compose_briefing_node(state: _AdvisorState, config: RunnableConfig) -> _AdvisorState:
    ctx = _ctx_of(config)
    reply = await compose_briefing(
        ctx,
        snapshot=state.get("snapshot") or _EMPTY_SNAPSHOT,
        demand=state.get("demand") or _EMPTY_DEMAND,
        churn=state.get("churn") or _EMPTY_CHURN,
        anomaly=state.get("anomaly") or _EMPTY_ANOMALY,
    )
    return {"reply": reply}


def _build_graph():
    graph: StateGraph = StateGraph(_AdvisorState)
    graph.add_node("gather_snapshot", _gather_snapshot)
    graph.add_node("gather_demand", _gather_demand)
    graph.add_node("gather_churn", _gather_churn)
    graph.add_node("gather_anomaly", _gather_anomaly)
    graph.add_node("compose_briefing", _compose_briefing_node)
    graph.add_edge(START, "gather_snapshot")
    graph.add_edge("gather_snapshot", "gather_demand")
    graph.add_edge("gather_demand", "gather_churn")
    graph.add_edge("gather_churn", "gather_anomaly")
    graph.add_edge("gather_anomaly", "compose_briefing")
    graph.add_edge("compose_briefing", END)
    return graph.compile()


class AdvisorAgent:
    """Morning briefing agent. Built once (lifespan); `handle` per owner question.

    Constitution IV: the graph's first four nodes each call exactly one Phase 6
    ML predictor (or a DB read) — no LLM. The fifth node receives the four
    structured outputs and calls the Tier 2 LLM ONLY to synthesize them into
    Lebanese Arabic. The LLM never produces a number, flag, or score.

    Read-only: the AdvisorAgent writes nothing to the DB and never commits.
    """

    def __init__(
        self,
        router: LLMRouter,
        settings: Settings,
        sessionmaker: async_sessionmaker,
        demand_predictor: DemandPredictor | None = None,
        churn_predictor: ChurnPredictor | None = None,
        anomaly_detector: AnomalyDetector | None = None,
    ) -> None:
        self._router = router
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._demand_predictor = demand_predictor or StubDemandPredictor()
        self._churn_predictor = churn_predictor or StubChurnPredictor()
        self._anomaly_detector = anomaly_detector or StubAnomalyDetector()
        self._graph = _build_graph()

    async def handle(self, question: str, tenant_id: UUID) -> str:
        """Handle an owner's request for a morning briefing or strategic overview.

        Opens its own session per call. Never commits (read-only path).
        Returns a Lebanese Arabic briefing string.
        """
        async with self._sessionmaker() as session:
            ctx = ToolContext(
                session=session,
                tenant_id=tenant_id,
                router=self._router,
                settings=self._settings,
                demand_predictor=self._demand_predictor,
                churn_predictor=self._churn_predictor,
                anomaly_detector=self._anomaly_detector,
            )
            log.info("advisor_agent.handle", tenant_id=str(tenant_id))
            final: _AdvisorState = await self._graph.ainvoke(
                {"question": question},
                config={"configurable": {"ctx": ctx}},
            )
            return final.get("reply", advisor_agent_ar.FALLBACK_BRIEFING)
