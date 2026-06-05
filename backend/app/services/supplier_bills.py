"""The supplier-bill lifecycle writer (Phase 5, Task 5.3).

`SupplierBillService` is the ONLY writer of bill state (mirrors PurchaseOrderService
being the only PO-state writer). Every transition validates the CURRENT status
against the lifecycle and is audited + leaves a per-bill breadcrumb; nothing else in
the app flips a bill's status.

Lifecycle (single source of truth for the gate + UI)::

    uploaded ──► ocr_processing ──► extracted ──approve──► committing ──► committed
        │              │              │   └─► rejected         └─commit fails─► extracted
        │              └─► ocr_failed │
        └──────────────────────────── (a human reviews `extracted`)

`committing` is the transient state between a human's approval and the gated
BillCommitter actually applying stock: the moment a bill is approved it leaves the
review list (which shows only `extracted`/`ocr_failed`), so it can never be
double-approved. If the committer fails, it reverts to `extracted` for re-review.
(This mirrors the PurchaseOrder `approved` state — the same window the PO loop closes
between approve and dispatch.)

Transactions: like AuditService and PurchaseOrderService, the write methods flush
but do NOT commit — they join the caller's transaction. The split matters for the
gate (constitution V): `approve()` only moves the row toward commit and returns it;
the signed `bill.commit` token is minted in the API layer (ActionGate, Task 5.11)
and the gated stock increase fires AFTER commit (Task 5.12). So `status` is never the
send authority — the token is.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.bills import BillRead
from app.db.models import Supplier, SupplierBill, SupplierBillEvent, SupplierBillLine
from app.repositories.supplier_bills import REVIEW_STATUSES, SupplierBillRepository
from app.services.audit import AuditService


def _to_read(bill: SupplierBill, supplier: Supplier | None) -> BillRead:
    """Map a bill (joined to its supplier, if mapped) to the review read model."""
    return BillRead(
        id=bill.id,
        status=bill.status,
        supplier_id=bill.supplier_id,
        supplier_name=supplier.name if supplier is not None else None,
        original_filename=bill.original_filename,
        bill_date=bill.bill_date,
        total_amount=bill.total_amount,
        currency=bill.currency,
        min_confidence=bill.min_confidence,
        reject_reason=bill.reject_reason,
        reviewed_at=bill.reviewed_at,
        committed_at=bill.committed_at,
        created_at=bill.created_at,
    )


class SupplierBillNotFound(Exception):
    """The bill id is not this tenant's (scoped lookup missed) or doesn't exist.

    Surfaces as a 404 in the API layer. A cross-tenant id lands here exactly the
    same way a deleted id does — the Wall never reveals that another tenant's bill
    exists (constitution I).
    """

    def __init__(self, bill_id: UUID) -> None:
        self.bill_id = bill_id
        super().__init__(f"supplier bill {bill_id} not found")


class InvalidBillTransition(Exception):
    """A transition was requested from a status that does not allow it.

    e.g. approving a bill that is not `extracted`, committing a `rejected` bill. The
    lifecycle is the single source of truth; an out-of-order transition is a 409,
    never a silent overwrite — the service is the ONLY bill-state writer and guards
    every move.
    """

    def __init__(self, bill_id: UUID, current: str, action: str) -> None:
        self.bill_id = bill_id
        self.current = current
        self.action = action
        super().__init__(f"cannot {action} supplier bill {bill_id} in status {current}")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SupplierBillService:
    """The ONLY writer of supplier-bill state. Every transition validates the
    CURRENT status against the lifecycle and is audited; nothing else flips a bill's
    status. Mirrors PurchaseOrderService — flushes, does not commit, joining the
    caller's transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = SupplierBillRepository(session)
        self._audit = AuditService(session)

    async def _load(self, tenant_id: UUID, bill_id: UUID) -> SupplierBill:
        bill = await self._repo.get(tenant_id, bill_id)
        if bill is None:
            raise SupplierBillNotFound(bill_id)
        return bill

    def _event(self, tenant_id: UUID, bill_id: UUID, event: str, detail: str | None = None) -> None:
        self._session.add(
            SupplierBillEvent(
                tenant_id=tenant_id, supplier_bill_id=bill_id, event=event, detail=detail
            )
        )

    async def list_for_review(
        self,
        *,
        tenant_id: UUID,
        statuses: Sequence[str] = REVIEW_STATUSES,
        limit: int,
        offset: int,
    ) -> tuple[int, list[BillRead]]:
        """One page of this tenant's review list plus the total count, both scoped.

        Joins each bill to its supplier (if mapped) for display — the JOIN is
        tenant-scoped on both sides in the repo (constitution I). Read-only; no
        commit (the caller's request transaction is closed by the session dep)."""
        total = await self._repo.count_for_review(tenant_id, statuses=statuses)
        rows = await self._repo.list_for_review(
            tenant_id, statuses=statuses, limit=limit, offset=offset
        )
        items = [_to_read(bill, supplier) for bill, supplier in rows]
        return total, items

    async def create_uploaded(
        self,
        *,
        tenant_id: UUID,
        object_key: str,
        original_filename: str | None,
        content_type: str | None,
        bill_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> SupplierBill:
        """Record a freshly uploaded bill in `uploaded`. The image is already in
        MinIO under `object_key`; the worker picks it up next. NEVER touches stock.
        Writes an `uploaded` breadcrumb and audits `bill.uploaded`.

        `bill_id` may be supplied so the caller can build the (tenant-prefixed)
        MinIO key from the id BEFORE the upload, then persist the row with that same
        id and key — one write, no NULL key window. Omitted in tests where the key
        is synthetic.
        """
        bill = SupplierBill(
            object_key=object_key,
            original_filename=original_filename,
            content_type=content_type,
            status="uploaded",
        )
        if bill_id is not None:
            bill.id = bill_id
        bill = await self._repo.add(tenant_id, bill)
        self._event(tenant_id, bill.id, "uploaded")
        await self._audit.record(
            tenant_id=tenant_id, actor_id=actor_id, action="bill.uploaded", target=str(bill.id)
        )
        await self._session.flush()
        return bill

    async def mark_processing(self, *, tenant_id: UUID, bill_id: UUID) -> SupplierBill:
        """The worker claimed the bill: `uploaded` → `ocr_processing`. Guards against
        double-claiming (a non-uploaded bill is a 409)."""
        bill = await self._load(tenant_id, bill_id)
        if bill.status != "uploaded":
            raise InvalidBillTransition(bill_id, bill.status, "process")
        bill.status = "ocr_processing"
        self._event(tenant_id, bill_id, "ocr_processing")
        await self._session.flush()
        return bill

    async def save_extraction(
        self,
        *,
        tenant_id: UUID,
        bill_id: UUID,
        ocr_engine: str,
        ocr_text: str,
        extracted: dict[str, Any] | None,
        lines: Sequence[SupplierBillLine],
        min_confidence: Decimal | None,
        supplier_id: UUID | None = None,
        bill_date: date | None = None,
        total_amount: Decimal | None = None,
        currency: str | None = None,
    ) -> SupplierBill:
        """OCR + extraction succeeded: `ocr_processing` → `extracted`, persisting the
        raw text, the structured BillData, the per-line rows, and the review signals
        (supplier/date/total/currency/min_confidence). The bill now awaits a human;
        NO stock change. Audited `bill.extracted` with an `extracted` breadcrumb."""
        bill = await self._load(tenant_id, bill_id)
        if bill.status != "ocr_processing":
            raise InvalidBillTransition(bill_id, bill.status, "extract")
        bill.status = "extracted"
        bill.ocr_engine = ocr_engine
        bill.ocr_text = ocr_text
        bill.extracted = extracted
        bill.supplier_id = supplier_id
        bill.bill_date = bill_date
        bill.total_amount = total_amount
        bill.currency = currency
        bill.min_confidence = min_confidence
        for line in lines:
            # Force the line into the bill's scope — never trust the caller to set
            # tenant_id/bill_id (the Wall, same discipline as the repo base.add).
            line.tenant_id = tenant_id
            line.supplier_bill_id = bill_id
            self._session.add(line)
        self._event(tenant_id, bill_id, "extracted", f"{len(lines)} lines")
        await self._audit.record(
            tenant_id=tenant_id, actor_id=None, action="bill.extracted", target=str(bill_id)
        )
        await self._session.flush()
        return bill

    async def mark_ocr_failed(self, *, tenant_id: UUID, bill_id: UUID, error: str) -> SupplierBill:
        """OCR/extraction failed: `ocr_processing` → `ocr_failed`. The bill is
        surfaced for retry / manual entry; the image stays in MinIO. Audited."""
        bill = await self._load(tenant_id, bill_id)
        if bill.status != "ocr_processing":
            raise InvalidBillTransition(bill_id, bill.status, "fail")
        bill.status = "ocr_failed"
        self._event(tenant_id, bill_id, "ocr_failed", error)
        await self._audit.record(
            tenant_id=tenant_id, actor_id=None, action="bill.ocr_failed", target=str(bill_id)
        )
        await self._session.flush()
        return bill

    async def approve(self, *, tenant_id: UUID, bill_id: UUID, approver_id: UUID) -> SupplierBill:
        """Move an `extracted` bill to `committing`, stamping the reviewer.

        `committing` takes the bill out of the review list at once, so it can't be
        double-approved while the gated commit runs. Returns the bill; the signed
        `bill.commit` token is minted in the API layer (ActionGate, Task 5.11) and the
        gated stock increase fires AFTER commit (Task 5.12). This service never moves
        stock — approval is the lifecycle marker, not the commit authority
        (constitution V): the bill becomes `committed` only when the BillCommitter
        clears the token and succeeds.
        """
        bill = await self._load(tenant_id, bill_id)
        if bill.status != "extracted":
            raise InvalidBillTransition(bill_id, bill.status, "approve")
        bill.status = "committing"
        bill.reviewed_by = approver_id
        bill.reviewed_at = _utcnow()
        self._event(tenant_id, bill_id, "approved")
        await self._audit.record(
            tenant_id=tenant_id, actor_id=approver_id, action="bill.approved", target=str(bill_id)
        )
        await self._session.flush()
        return bill

    async def reject(
        self, *, tenant_id: UUID, bill_id: UUID, approver_id: UUID, reason: str
    ) -> SupplierBill:
        """Decline an `extracted` bill. reason is required (enforced here AND at the
        API). Provisions nothing — a rejected bill never commits; the image stays in
        MinIO with the reason recorded."""
        bill = await self._load(tenant_id, bill_id)
        if bill.status != "extracted":
            raise InvalidBillTransition(bill_id, bill.status, "reject")
        bill.status = "rejected"
        bill.reviewed_by = approver_id
        bill.reviewed_at = _utcnow()
        bill.reject_reason = reason
        self._event(tenant_id, bill_id, "rejected", reason)
        await self._audit.record(
            tenant_id=tenant_id, actor_id=approver_id, action="bill.rejected", target=str(bill_id)
        )
        await self._session.flush()
        return bill

    async def mark_committed(self, *, tenant_id: UUID, bill_id: UUID) -> SupplierBill:
        """The gated BillCommitter applied every validated line to stock:
        `committing` → `committed`. Records the commit time. Audited `bill.committed`.
        Called ONLY by the committer, behind the signed token — never reachable from a
        status flip alone (constitution V)."""
        bill = await self._load(tenant_id, bill_id)
        if bill.status != "committing":
            raise InvalidBillTransition(bill_id, bill.status, "commit")
        bill.status = "committed"
        bill.committed_at = _utcnow()
        self._event(tenant_id, bill_id, "committed")
        await self._audit.record(
            tenant_id=tenant_id, actor_id=None, action="bill.committed", target=str(bill_id)
        )
        await self._session.flush()
        return bill

    async def mark_line_committed(self, *, tenant_id: UUID, line: SupplierBillLine) -> None:
        """Flag one bill line as applied to stock (set during the gated commit).

        Tenant-scoped defensively: the line is only flagged if it belongs to this
        tenant (it always does — the committer loads lines tenant-scoped — but the
        guard keeps the Wall explicit). Audited `bill.line_committed`. Flushes, does
        not commit (joins the committer's transaction)."""
        if line.tenant_id != tenant_id:
            return
        line.committed = True
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=None,
            action="bill.line_committed",
            target=str(line.id),
        )
        await self._session.flush()

    async def revert_to_extracted(
        self, *, tenant_id: UUID, bill_id: UUID, error: str
    ) -> SupplierBill:
        """The gated commit failed: `committing` → `extracted` so the owner can
        re-review and re-approve (no stock moved — the committer rolls back on any
        failure). Audited `bill.commit_failed` with the reason. Called ONLY by the
        committer."""
        bill = await self._load(tenant_id, bill_id)
        if bill.status != "committing":
            raise InvalidBillTransition(bill_id, bill.status, "revert")
        bill.status = "extracted"
        self._event(tenant_id, bill_id, "commit_failed", error)
        await self._audit.record(
            tenant_id=tenant_id, actor_id=None, action="bill.commit_failed", target=str(bill_id)
        )
        await self._session.flush()
        return bill
