"""The background OCR worker (Phase 5, Task 5.8) — the project's first worker.

Phase 4 deferred workers to here. This process polls for uploaded supplier bills and
runs the OCR pipeline OUT of the api container (ROADMAP pitfall: OCR takes seconds; it
must never block a request):

    claim uploaded bill ──► fetch image from MinIO ──► preprocess
       ──► OCREngine.extract (Cloud Vision; stub in CI) ──► BillExtractionAgent
       ──► persist lines + per-field confidence ──► status `extracted`
                                                 (or `ocr_failed` on any failure)

It runs the SAME image as the api, a different entrypoint (`python -m app.worker`),
and builds its own singletons exactly the way `lifespan` does (the shared builder,
`build_pipeline`), so the api and the worker construct the engine/agent/storage
identically. Each bill is processed tenant-scoped; the one cross-tenant query
(`tenants_with_claimable_bills`) only discovers WHICH tenants have work, then the
worker re-enters the Wall per tenant.

Poll-based for now (no Celery — constitution: the simplest design that satisfies the
rules; the bill row is the source of truth, so a missed pass is recoverable next
tick). Phase 8 may move this to a durable queue. SIGTERM/SIGINT trigger a graceful
shutdown between passes.
"""

import asyncio
import signal
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.llm.router import GeminiRouter
from app.agents.ocr.agent import BillExtractionAgent, ExtractionResult
from app.db.models import BusinessPolicy, SupplierBillLine
from app.db.session import create_engine
from app.infra.embeddings import EmbeddingClient, build_embedding_client
from app.infra.kb_content import build_kb_text, chunk_text
from app.infra.logging import configure_logging, get_logger
from app.infra.ocr import build_ocr_engine, preprocess
from app.infra.ocr.engine import OCREngine
from app.infra.settings import Settings, get_settings
from app.infra.storage import StorageClient
from app.infra.vault import resolve_secrets
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.business_policies import BusinessPolicyRepository
from app.repositories.knowledge_base_docs import (
    KnowledgeBaseDocRepository,
    tenants_with_embeddable_docs,
)
from app.repositories.supplier_bills import (
    SupplierBillRepository,
    tenants_with_claimable_bills,
)
from app.repositories.vector_chunks import VectorChunkRepository
from app.services.supplier_bills import SupplierBillService

_COST_ALERT_INTERVAL = 3600.0  # seconds between full cost-alert sweeps

log = get_logger(__name__)


@dataclass
class BillProcessor:
    """Runs the OCR pipeline for claimable bills, tenant by tenant.

    Holds the singletons (storage, ocr_engine, bill_agent) and opens its own session
    per unit of work from the injected sessionmaker — exactly like the agents and the
    dispatcher. Separated from the poll loop so it is unit-testable without timers or
    signals.
    """

    sessionmaker: async_sessionmaker
    storage: StorageClient
    ocr_engine: OCREngine
    bill_agent: BillExtractionAgent
    settings: Settings

    async def run_once(self) -> int:
        """Process one pass over every tenant that has claimable bills.

        Returns the number of bills processed (success or failure) — handy for tests
        and for deciding whether the loop did work. Discovers tenants via the one
        cross-tenant query, then drains each tenant's queue tenant-scoped.
        """
        async with self.sessionmaker() as session:
            tenant_ids = await tenants_with_claimable_bills(session)

        processed = 0
        for tenant_id in tenant_ids:
            processed += await self._process_tenant(tenant_id)
        return processed

    async def _process_tenant(self, tenant_id: UUID) -> int:
        """Claim and process up to a batch of this tenant's uploaded bills."""
        async with self.sessionmaker() as session:
            repo = SupplierBillRepository(session)
            bills = await repo.list_claimable(tenant_id, limit=self.settings.worker_batch_size)
            bill_ids = [b.id for b in bills]

        count = 0
        for bill_id in bill_ids:
            await self._process_one(tenant_id, bill_id)
            count += 1
        return count

    async def _process_one(self, tenant_id: UUID, bill_id: UUID) -> None:
        """OCR + extract one bill, end to end, with its own transaction(s).

        Any failure in fetch/preprocess/OCR/extract lands the bill in `ocr_failed`
        (recorded in its own transaction) rather than crashing the worker — the row
        is the source of truth and the owner can retry from review.
        """
        # 1. Claim it (uploaded → ocr_processing) in its own transaction. If another
        # pass already claimed it (not `uploaded`), skip quietly.
        async with self.sessionmaker() as session:
            svc = SupplierBillService(session)
            bill = await SupplierBillRepository(session).get(tenant_id, bill_id)
            if bill is None or bill.status != "uploaded":
                return
            object_key = bill.object_key
            await svc.mark_processing(tenant_id=tenant_id, bill_id=bill_id)
            await session.commit()

        # 2. Fetch + preprocess + OCR + extract OUTSIDE any DB transaction (slow I/O).
        try:
            image = await self.storage.get_bytes(object_key)
            cleaned = preprocess(image)
            ocr = await self.ocr_engine.extract(cleaned)
            ocr_conf = ocr.min_confidence if ocr.min_confidence is not None else 1.0
            result = await self.bill_agent.extract_for_bill(
                tenant_id, ocr.text, ocr_confidence=ocr_conf
            )
        except Exception as exc:  # noqa: BLE001 — any pipeline failure → ocr_failed
            log.warning(
                "worker.bill.failed",
                tenant_id=str(tenant_id),
                bill_id=str(bill_id),
                error=f"{type(exc).__name__}: {exc}",
            )
            async with self.sessionmaker() as session:
                await SupplierBillService(session).mark_ocr_failed(
                    tenant_id=tenant_id, bill_id=bill_id, error=f"{type(exc).__name__}: {exc}"
                )
                await session.commit()
            return

        # 3. Persist the extraction (ocr_processing → extracted) in its own txn.
        await self._save(tenant_id, bill_id, ocr.engine, ocr.text, result)
        log.info(
            "worker.bill.extracted",
            tenant_id=str(tenant_id),
            bill_id=str(bill_id),
            lines=len(result.lines),
            min_confidence=result.min_confidence,
        )

    async def _save(
        self,
        tenant_id: UUID,
        bill_id: UUID,
        ocr_engine: str,
        ocr_text: str,
        result: ExtractionResult,
    ) -> None:
        """Build the SupplierBillLine rows from the extraction and save it."""
        lines = [
            SupplierBillLine(
                raw_text=line.data.raw_text,
                name_ar=line.data.name_ar,
                quantity=line.data.quantity,
                unit=line.data.unit,
                unit_amount=line.data.unit_amount,
                line_amount=line.data.line_amount,
                confidence=Decimal(str(line.confidence)),
                product_id=line.product_id,
            )
            for line in result.lines
        ]
        min_conf = (
            Decimal(str(result.min_confidence)) if result.min_confidence is not None else None
        )
        async with self.sessionmaker() as session:
            await SupplierBillService(session).save_extraction(
                tenant_id=tenant_id,
                bill_id=bill_id,
                ocr_engine=ocr_engine,
                ocr_text=ocr_text,
                extracted=result.data.model_dump(mode="json"),
                lines=lines,
                min_confidence=min_conf,
                currency=result.data.currency,
                total_amount=result.data.total_amount,
            )
            await session.commit()


@dataclass
class KnowledgeEmbedder:
    """Drains `knowledge_base_docs` (pending/stale) into the `knowledge` vector
    corpus, tenant by tenant — the consumer the tracking layer has waited for since
    Phase 1/3. Holds the embedding client; opens its own session per unit of work.
    Separated from the poll loop so it is unit-testable.
    """

    sessionmaker: async_sessionmaker
    embedding_client: EmbeddingClient
    settings: Settings

    async def run_once(self) -> int:
        """One pass over every tenant with embeddable docs. Returns the count
        embedded (or handled). Discovers tenants via the one cross-tenant query, then
        drains each tenant-scoped."""
        async with self.sessionmaker() as session:
            tenant_ids = await tenants_with_embeddable_docs(session)

        embedded = 0
        for tenant_id in tenant_ids:
            embedded += await self._embed_tenant(tenant_id)
        return embedded

    async def _embed_tenant(self, tenant_id: UUID) -> int:
        async with self.sessionmaker() as session:
            docs = await KnowledgeBaseDocRepository(session).list_embeddable(
                tenant_id, limit=self.settings.worker_batch_size
            )
            work = [(d.id, d.source_type, d.source_id, d.content_hash) for d in docs]

        count = 0
        for doc_id, source_type, source_id, content_hash in work:
            await self._embed_one(tenant_id, doc_id, source_type, source_id, content_hash)
            count += 1
        return count

    async def _embed_one(
        self, tenant_id: UUID, doc_id: UUID, source_type: str, source_id: UUID, content_hash
    ) -> None:
        """Embed one KB doc: build text → chunk → embed → upsert chunks → mark
        embedded, all in one tenant-scoped transaction. A failure is logged and the
        doc is left pending/stale to retry next pass (never crashes the worker).

        A committed supplier bill (source_type='bill') is embedded into the SEPARATE
        `bills` corpus (Phase 6 forecasting context); everything else goes to the
        `knowledge` corpus (products/policies/hours). The same tracking row + drain
        path serves both — no extra table."""
        corpus = "bills" if source_type == "bill" else "knowledge"
        try:
            async with self.sessionmaker() as session:
                text = await build_kb_text(session, tenant_id, source_type, source_id)
                vectors_repo = VectorChunkRepository(session)
                kb_repo = KnowledgeBaseDocRepository(session)

                if text is None:
                    # The source vanished — drop any prior chunks, mark the doc handled.
                    await vectors_repo.delete_source(
                        tenant_id,
                        corpus=corpus,
                        source_type=source_type,
                        source_id=source_id,
                    )
                    await kb_repo.mark_embedded(tenant_id, doc_id)
                    await session.commit()
                    return

                chunks = chunk_text(text)
                embeddings = await self.embedding_client.embed(chunks)
                await vectors_repo.upsert_chunks(
                    tenant_id,
                    corpus=corpus,
                    source_type=source_type,
                    source_id=source_id,
                    content_hash=content_hash,
                    chunks=list(zip(chunks, embeddings, strict=True)),
                )
                await kb_repo.mark_embedded(tenant_id, doc_id)
                await session.commit()
            log.info(
                "worker.kb.embedded",
                tenant_id=str(tenant_id),
                corpus=corpus,
                source_type=source_type,
                source_id=str(source_id),
                chunks=len(chunks),
            )
        except Exception as exc:  # noqa: BLE001 — leave pending/stale to retry
            log.warning(
                "worker.kb.failed",
                tenant_id=str(tenant_id),
                source_type=source_type,
                source_id=str(source_id),
                error=f"{type(exc).__name__}: {exc}",
            )


class CostAlertTask:
    """Hourly task: check each tenant's LLM spend against their daily budget.

    When today_usd ≥ budget_usd and an alert_webhook_url is configured, POSTs a
    JSON payload to that URL. Deduplicates via a `cost_alert_last_sent` policy row —
    if the last alert was sent within the past hour, the alert is suppressed.
    Failures (unreachable webhook, bad URL) are logged and swallowed — the alert
    system must never crash the worker or degrade order processing.
    """

    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def run_once(self) -> int:
        """Sweep all tenants that have a non-zero budget. Returns alerts sent."""
        async with self._sessionmaker() as session:
            tenant_ids = await self._tenants_with_budget(session)

        alerts = 0
        for tenant_id in tenant_ids:
            if await self._check_and_alert(tenant_id):
                alerts += 1
        return alerts

    async def _tenants_with_budget(self, session: AsyncSession) -> list[UUID]:
        stmt = select(BusinessPolicy.tenant_id).where(
            BusinessPolicy.key == "daily_llm_budget_usd",
            BusinessPolicy.value.isnot(None),
            BusinessPolicy.value != "0",
            BusinessPolicy.value != "",
        )
        rows = (await session.execute(stmt)).all()
        return [row[0] for row in rows]

    async def _check_and_alert(self, tenant_id: UUID) -> bool:
        async with self._sessionmaker() as session:
            policy_repo = BusinessPolicyRepository(session)

            budget_policy = await policy_repo.get_by_key(tenant_id, "daily_llm_budget_usd")
            if not budget_policy or not budget_policy.value:
                return False
            try:
                budget_usd = float(budget_policy.value)
            except ValueError:
                return False
            if budget_usd <= 0:
                return False

            today = date.today()
            summary = await AgentRunRepository(session).daily_summary(
                tenant_id, from_date=today, to_date=today
            )
            today_usd = sum(sum(agents.values()) for agents in summary.values())

            if today_usd < budget_usd:
                return False

            # Deduplicate: suppress if last alert < 1 hour ago
            last_policy = await policy_repo.get_by_key(tenant_id, "cost_alert_last_sent")
            if last_policy and last_policy.value:
                try:
                    last_sent = datetime.fromisoformat(last_policy.value)
                    if (datetime.now(UTC) - last_sent).total_seconds() < 3600:
                        return False
                except ValueError:
                    pass

            webhook_policy = await policy_repo.get_by_key(tenant_id, "alert_webhook_url")
            if not webhook_policy or not webhook_policy.value:
                return False

            await self._post_alert(webhook_policy.value, tenant_id, today_usd, budget_usd)

            # Record the alert time
            now_iso = datetime.now(UTC).isoformat()
            if last_policy:
                last_policy.value = now_iso
            else:
                session.add(
                    BusinessPolicy(
                        tenant_id=tenant_id,
                        key="cost_alert_last_sent",
                        value=now_iso,
                    )
                )
            await session.commit()
            return True

    @staticmethod
    async def _post_alert(url: str, tenant_id: UUID, today_usd: float, budget_usd: float) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    url,
                    json={
                        "tenant_id": str(tenant_id),
                        "today_usd": round(today_usd, 6),
                        "budget_usd": budget_usd,
                        "message": (
                            f"تجاوز الإنفاق اليومي على الذكاء الاصطناعي الميزانية المحددة "
                            f"({today_usd:.4f} USD من {budget_usd:.2f} USD)."
                        ),
                    },
                )
            log.info("cost_alert.sent", tenant_id=str(tenant_id), today_usd=today_usd)
        except Exception as exc:
            log.warning("cost_alert.webhook_failed", tenant_id=str(tenant_id), error=str(exc))


def build_pipeline(settings: Settings) -> tuple[BillProcessor, KnowledgeEmbedder]:
    """Construct the worker's singletons the SAME way `lifespan` builds the api's.

    The engine, router, OCR engine, bill agent, storage, and embedding client are all
    built once here (constitution IV: agents/models load once). Returns the OCR bill
    processor and the KB embedder, sharing one sessionmaker. (Storage bucket is ensured
    by the api at startup; the worker only reads, so it does not re-ensure.)
    """
    engine = create_engine()
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    router = GeminiRouter(settings)
    processor = BillProcessor(
        sessionmaker=sessionmaker,
        storage=StorageClient(settings),
        ocr_engine=build_ocr_engine(settings),
        bill_agent=BillExtractionAgent(router, settings, sessionmaker),
        settings=settings,
    )
    embedder = KnowledgeEmbedder(
        sessionmaker=sessionmaker,
        embedding_client=build_embedding_client(settings),
        settings=settings,
    )
    return processor, embedder, CostAlertTask(sessionmaker)


async def run_worker() -> None:
    """The poll loop: build singletons, then process passes until asked to stop.

    SIGTERM/SIGINT set a stop event so the loop exits cleanly between passes (never
    mid-bill). Between passes it sleeps `worker_poll_seconds`. A pass that raises is
    logged and the loop continues — one bad pass must not kill the worker.
    """
    configure_logging()
    settings = resolve_secrets(get_settings())
    log.info(
        "worker.startup",
        ocr_mode=settings.ocr_mode,
        embedding_mode=settings.embedding_mode,
        poll=settings.worker_poll_seconds,
    )

    processor, embedder, cost_alert = build_pipeline(settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # add_signal_handler is unavailable on Windows for some signals — the
            # process is killed directly there; the graceful path is for Linux (prod).
            pass

    last_cost_check = time.monotonic() - _COST_ALERT_INTERVAL  # run on first pass

    while not stop.is_set():
        try:
            bills = await processor.run_once()
            docs = await embedder.run_once()
            if bills or docs:
                log.info("worker.pass.done", bills=bills, docs=docs)
        except Exception as exc:  # noqa: BLE001 — never let one pass kill the loop
            log.error("worker.pass.error", error=f"{type(exc).__name__}: {exc}")

        if time.monotonic() - last_cost_check >= _COST_ALERT_INTERVAL:
            try:
                alerts = await cost_alert.run_once()
                if alerts:
                    log.info("cost_alert.sweep.done", alerts_sent=alerts)
            except Exception as exc:  # noqa: BLE001
                log.error("cost_alert.sweep.error", error=f"{type(exc).__name__}: {exc}")
            last_cost_check = time.monotonic()

        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            pass  # poll interval elapsed → next pass

    log.info("worker.shutdown")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
