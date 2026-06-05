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
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.llm.router import GeminiRouter
from app.agents.ocr.agent import BillExtractionAgent, ExtractionResult
from app.db.models import SupplierBillLine
from app.db.session import create_engine
from app.infra.embeddings import EmbeddingClient, build_embedding_client
from app.infra.kb_content import build_kb_text, chunk_text
from app.infra.logging import configure_logging, get_logger
from app.infra.ocr import build_ocr_engine, preprocess
from app.infra.ocr.engine import OCREngine
from app.infra.settings import Settings, get_settings
from app.infra.storage import StorageClient
from app.infra.vault import resolve_secrets
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
    return processor, embedder


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

    processor, embedder = build_pipeline(settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # add_signal_handler is unavailable on Windows for some signals — the
            # process is killed directly there; the graceful path is for Linux (prod).
            pass

    while not stop.is_set():
        try:
            bills = await processor.run_once()
            docs = await embedder.run_once()
            if bills or docs:
                log.info("worker.pass.done", bills=bills, docs=docs)
        except Exception as exc:  # noqa: BLE001 — never let one pass kill the loop
            log.error("worker.pass.error", error=f"{type(exc).__name__}: {exc}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            pass  # poll interval elapsed → next pass

    log.info("worker.shutdown")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
