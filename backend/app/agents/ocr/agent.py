"""BillExtractionAgent — a standalone LangGraph StateGraph that structures a bill.

Flow:  extract (Tier-1 LLM: OCR text → BillData) → map (lines → catalog products)
       → assemble (combine OCR + extraction confidence into per-line scores)

It mirrors the InventoryAgent / OrderAgent EXACTLY so Phase 7 can drop it under the
supervisor without a rewrite: a compiled graph built ONCE (in lifespan), opening its
own DB session per call from an injected sessionmaker, with the per-call ToolContext
passed through the graph's `config` — never stored on the instance, so the single
lifespan-built agent is concurrency-safe.

The agent only EXTRACTS + MAPS. It NEVER commits to stock; that is the human-gated
BillCommitter (Task 5.11). The worker (Task 5.8) calls `extract_for_bill` and
persists the result via SupplierBillService.save_extraction.
"""

from dataclasses import dataclass, field
from typing import TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.llm.router import LLMRouter
from app.agents.ocr.schemas import BillData, BillLineData
from app.agents.ocr.tools import ToolContext, extract_bill, map_lines_to_products
from app.infra.logging import get_logger
from app.infra.ocr import confidence as C
from app.infra.settings import Settings

log = get_logger(__name__)


@dataclass
class ExtractedLine:
    """One assembled bill line: the extracted data, its mapped product (or None), and
    the combined OCR×extraction confidence the worker stores per line."""

    data: BillLineData
    product_id: UUID | None
    confidence: float


@dataclass
class ExtractionResult:
    """Everything the worker needs to persist a bill extraction (Task 5.8):
    the structured BillData, the per-line assembly, and the bill-level min
    confidence. `currency`/`total`/`bill_date_raw` are surfaced for convenience."""

    data: BillData
    lines: list[ExtractedLine] = field(default_factory=list)
    min_confidence: float | None = None


class _BillState(TypedDict, total=False):
    ocr_text: str
    ocr_confidence: float
    data: BillData
    mapped: list[UUID | None]
    result: ExtractionResult


def _ctx_of(config: RunnableConfig) -> ToolContext:
    return config["configurable"]["ctx"]


# ---- Graph nodes (module-level; per-call context comes via config) ----
async def _extract(state: _BillState, config: RunnableConfig) -> _BillState:
    data = await extract_bill(_ctx_of(config), state["ocr_text"])
    return {"data": data}


async def _map(state: _BillState, config: RunnableConfig) -> _BillState:
    mapped = await map_lines_to_products(_ctx_of(config), state["data"])
    return {"mapped": mapped}


def _assemble(state: _BillState, config: RunnableConfig) -> _BillState:
    """Combine each line's extraction certainty with the OCR confidence into the
    per-line score (Task 5.6 formula), and compute the bill's min confidence."""
    data = state["data"]
    mapped = state["mapped"]
    ocr_conf = state.get("ocr_confidence", 1.0)
    lines: list[ExtractedLine] = []
    for line, product_id in zip(data.lines, mapped, strict=True):
        score = C.field_confidence(ocr_conf, line.certainty)
        lines.append(ExtractedLine(data=line, product_id=product_id, confidence=score))
    min_conf = C.min_confidence([line.confidence for line in lines])
    return {"result": ExtractionResult(data=data, lines=lines, min_confidence=min_conf)}


def _build_graph():
    graph: StateGraph = StateGraph(_BillState)
    graph.add_node("extract", _extract)
    graph.add_node("map", _map)
    graph.add_node("assemble", _assemble)

    graph.add_edge(START, "extract")
    graph.add_edge("extract", "map")
    graph.add_edge("map", "assemble")
    graph.add_edge("assemble", END)
    return graph.compile()


class BillExtractionAgent:
    """Bill-structuring graph. Built once (lifespan); `extract_for_bill` is called
    per uploaded bill by the worker (Task 5.8)."""

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

    async def extract_for_bill(
        self, tenant_id: UUID, ocr_text: str, *, ocr_confidence: float = 1.0
    ) -> ExtractionResult:
        """Structure one bill's OCR text and map its lines, returning the assembled
        ExtractionResult the worker persists.

        Opens its own session (it runs outside any request — in the worker). The
        per-call ToolContext is passed via config, so the shared graph stays
        concurrency-safe. Read-only on the catalog (mapping never writes), so no
        commit here; the worker owns the persistence transaction.
        """
        async with self._sessionmaker() as session:
            ctx = ToolContext(
                session=session,
                tenant_id=tenant_id,
                router=self._router,
                settings=self._settings,
            )
            log.info(
                "bill_agent.extract_for_bill",
                tenant_id=str(tenant_id),
                ocr_chars=len(ocr_text),
            )
            final: _BillState = await self._graph.ainvoke(
                {"ocr_text": ocr_text, "ocr_confidence": ocr_confidence},
                config={"configurable": {"ctx": ctx}},
            )
            return final["result"]
