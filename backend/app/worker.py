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
from app.infra.logging import configure_logging, get_logger
from app.infra.ocr import build_ocr_engine, preprocess
from app.infra.ocr.engine import OCREngine
from app.infra.settings import Settings, get_settings
from app.infra.storage import StorageClient
from app.infra.vault import resolve_secrets
from app.repositories.supplier_bills import (
    SupplierBillRepository,
    tenants_with_claimable_bills,
)
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


def build_pipeline(settings: Settings) -> BillProcessor:
    """Construct the worker's singletons the SAME way `lifespan` builds the api's.

    The engine, router, OCR engine, bill agent, and storage are all built once here
    (constitution IV: agents/models load once). Returns a BillProcessor wired with
    its own sessionmaker. (Storage bucket is ensured by the api at startup; the worker
    only reads, so it does not re-ensure.)
    """
    engine = create_engine()
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    router = GeminiRouter(settings)
    return BillProcessor(
        sessionmaker=sessionmaker,
        storage=StorageClient(settings),
        ocr_engine=build_ocr_engine(settings),
        bill_agent=BillExtractionAgent(router, settings, sessionmaker),
        settings=settings,
    )


async def run_worker() -> None:
    """The poll loop: build singletons, then process passes until asked to stop.

    SIGTERM/SIGINT set a stop event so the loop exits cleanly between passes (never
    mid-bill). Between passes it sleeps `worker_poll_seconds`. A pass that raises is
    logged and the loop continues — one bad pass must not kill the worker.
    """
    configure_logging()
    settings = resolve_secrets(get_settings())
    log.info("worker.startup", ocr_mode=settings.ocr_mode, poll=settings.worker_poll_seconds)

    processor = build_pipeline(settings)

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
            n = await processor.run_once()
            if n:
                log.info("worker.pass.done", processed=n)
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
