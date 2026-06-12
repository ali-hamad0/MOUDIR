"""Supplier-bill upload + review API (Phase 5, Tasks 5.4 + 5.12).

The owner photographs a paper supplier bill from the dashboard. `POST /bills`
streams the image straight to MinIO under a tenant-prefixed key and records the
bill as `uploaded`, returning 202 immediately — OCR is NEVER run in the request
(it takes seconds; the worker, Task 5.8, does it). `GET /bills` is the tenant-scoped
review list; `GET /bills/{id}` is the side-by-side review screen (image URL + lines).

The review flow is the HIL gate for bills (constitution V), mirroring the Phase 4
approvals API:

    approve  ──► SupplierBillService.approve (→ committing) + COMMIT
             ──► mint_approval_token(bill.commit, bill_id, tenant, approver)
             ──► BackgroundTasks: BillCommitter.commit(bill, token)
             ──► return immediately

The commit runs AFTER the approve commit and OUT OF BAND (a background task), so a
slow/failed commit can never hang the approve call; if it fails its retries the bill
reverts to `extracted` and reappears in the review list. The bill→stock side effect
runs ONLY behind the signed token, which BillCommitter re-verifies (the gate is at
the boundary that side-effects, never trusted from the caller).

Every route is tenant-scoped via `get_current_user` (tenant_id from the JWT, never
the path/body — the Wall, constitution I). The MinIO object key is built server-side
from the tenant + a freshly minted bill id; it is never accepted from the client, so
an upload can only ever land under the owning tenant's prefix.
"""

from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.bills import (
    ApproveBillRequest,
    BillDetail,
    BillLineRead,
    BillLinesUpdate,
    BillsPage,
    BillUploadAccepted,
    RejectBillRequest,
)
from app.db.models import Product, SupplierBillLine, User
from app.db.session import get_db_session
from app.infra.action_gate import mint_approval_token
from app.infra.bill_committer import COMMIT_ACTION
from app.infra.logging import get_logger
from app.infra.settings import Settings, get_settings
from app.infra.storage import StorageClient
from app.repositories.supplier_bills import LIFECYCLE_STATUSES, SupplierBillRepository
from app.services.plan_gate import (
    FREE_MAX_BILLS_PER_MONTH,
    require_within_limit,
    start_of_month,
)
from app.services.supplier_bills import (
    InvalidBillTransition,
    LineUpdate,
    SupplierBillNotFound,
    SupplierBillService,
)
from prompts import bills_ar

router = APIRouter(tags=["bills"])
log = get_logger(__name__)

# Tenant scope comes from the authenticated user's JWT, never the request body.
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]
Config = Annotated[Settings, Depends(get_settings)]

# Read the upload in bounded chunks so a hostile/accidental huge file is rejected
# before it is fully buffered, rather than loaded whole into memory.
_CHUNK = 1024 * 1024  # 1 MiB


async def _read_within_limit(upload: UploadFile, max_bytes: int) -> bytes:
    """Read the upload into memory, aborting with 413 if it exceeds max_bytes.

    Streams in chunks and stops as soon as the cap is crossed, so an oversize file
    never gets fully buffered. A bill is a phone photo (a few MiB), so buffering the
    validated bytes is fine; true streaming-to-storage is a later optimization.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"bill image exceeds the {max_bytes} byte limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/bills",
    response_model=BillUploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_bill(
    request: Request,
    user: CurrentUser,
    db: Db,
    settings: Config,
    file: Annotated[UploadFile, File()],
) -> BillUploadAccepted:
    """Stream a supplier-bill photo to MinIO and queue it for OCR.

    Validates the content type and size, builds a tenant-prefixed object key from a
    freshly minted bill id, streams the bytes to MinIO, records the bill as
    `uploaded`, and returns 202. OCR runs later in the worker — this call never
    blocks on it. tenant_id comes from the JWT; the key is built server-side, so the
    object can only land under this tenant's prefix (the Wall).
    """
    # Free plan: a monthly OCR-bill quota (plan_gate) — Pro is unlimited.
    uploaded_this_month = await SupplierBillRepository(db).count_created_since(
        user.tenant_id, start_of_month()
    )
    await require_within_limit(
        db, user.tenant_id, "bills", uploaded_this_month, FREE_MAX_BILLS_PER_MONTH
    )

    content_type = (file.content_type or "").lower()
    if content_type not in settings.bill_upload_allowed_content_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported bill image type: {content_type or 'unknown'}",
        )

    data = await _read_within_limit(file, settings.bill_upload_max_bytes)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")

    tenant_id = user.tenant_id
    bill_id = uuid4()
    # The key is built from tenant + bill id BEFORE the upload — the Wall for object
    # storage. Never derived from the (untrusted) filename beyond a sanitized suffix.
    key = StorageClient.object_key(tenant_id, bill_id, file.filename)

    storage: StorageClient = request.app.state.storage
    await storage.put_stream(key, data, content_type)

    # Persist the row with the SAME id + key — one write, no NULL-key window. The
    # upload is its own unit of work; commit it so the worker can claim it.
    bill = await SupplierBillService(db).create_uploaded(
        tenant_id=tenant_id,
        bill_id=bill_id,
        object_key=key,
        original_filename=file.filename,
        content_type=content_type,
        actor_id=user.id,
    )
    await db.commit()

    log.info(
        "bills.uploaded",
        tenant_id=str(tenant_id),
        bill_id=str(bill.id),
        content_type=content_type,
        size=len(data),
    )
    return BillUploadAccepted(id=bill.id, status=bill.status)


@router.get("/bills", response_model=BillsPage)
async def list_bills(
    request: Request,
    user: CurrentUser,
    db: Db,
    settings: Config,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BillsPage:
    """This tenant's bill list across the whole lifecycle — a fresh scan appears
    immediately (uploaded/processing), review candidates (`extracted`/`ocr_failed`)
    are clickable, and recently approved/rejected bills stay visible (Phase 10).
    Joined to supplier, newest first; scoped to the JWT tenant — never shows
    another tenant's bills (the Wall). Each item carries a presigned thumbnail URL
    so the list shows the scan itself; the key is tenant-prefixed, so a URL can
    only ever point at this tenant's object."""
    storage: StorageClient = request.app.state.storage
    ttl = timedelta(minutes=settings.bill_image_url_ttl_minutes)

    async def _presign(object_key: str) -> str:
        return await storage.presigned_get(object_key, ttl)

    total, items = await SupplierBillService(db).list_for_review(
        tenant_id=user.tenant_id,
        statuses=LIFECYCLE_STATUSES,
        limit=limit,
        offset=offset,
        presign=_presign,
    )
    return BillsPage(items=items, total=total, limit=limit, offset=offset)


def _to_line_read(line: SupplierBillLine, product: Product | None) -> BillLineRead:
    return BillLineRead(
        id=line.id,
        raw_text=line.raw_text,
        name_ar=line.name_ar,
        quantity=line.quantity,
        unit=line.unit,
        unit_amount=line.unit_amount,
        line_amount=line.line_amount,
        confidence=line.confidence,
        product_id=line.product_id,
        product_name_ar=product.name_ar if product is not None else None,
        committed=line.committed,
    )


async def _detail(
    request: Request, settings: Settings, tenant_id: UUID, bill, supplier, lines
) -> BillDetail:
    """Assemble the review detail, adding a presigned image URL from the tenant-
    prefixed object key (storage is an infra concern owned by the route, not the
    service). The URL is time-boxed by bill_image_url_ttl_minutes."""
    storage: StorageClient = request.app.state.storage
    image_url = await storage.presigned_get(
        bill.object_key, timedelta(minutes=settings.bill_image_url_ttl_minutes)
    )
    return BillDetail(
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
        image_url=image_url,
        lines=[_to_line_read(line, product) for line, product in lines],
    )


@router.get("/bills/{bill_id}", response_model=BillDetail)
async def get_bill(
    bill_id: UUID, request: Request, user: CurrentUser, db: Db, settings: Config
) -> BillDetail:
    """The review screen for one bill: header fields, a presigned image URL, and the
    extracted lines (each with its mapped product and confidence). 404 if the bill
    isn't this tenant's (the Wall — never reveals another tenant's bill exists)."""
    try:
        bill, supplier, lines = await SupplierBillService(db).get_with_lines(
            tenant_id=user.tenant_id, bill_id=bill_id
        )
    except SupplierBillNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, bills_ar.BILL_NOT_FOUND) from None
    return await _detail(request, settings, user.tenant_id, bill, supplier, lines)


@router.put("/bills/{bill_id}/lines", response_model=BillDetail)
async def update_bill_lines(
    bill_id: UUID,
    payload: BillLinesUpdate,
    request: Request,
    user: CurrentUser,
    db: Db,
    settings: Config,
) -> BillDetail:
    """Apply the owner's line corrections (edited fields + product mapping). Only an
    `extracted` bill is editable (409 otherwise). Returns the refreshed detail. 404
    if the bill isn't this tenant's."""
    tenant_id = user.tenant_id
    svc = SupplierBillService(db)
    updates = [
        LineUpdate(
            id=u.id,
            name_ar=u.name_ar,
            quantity=u.quantity,
            unit=u.unit,
            unit_amount=u.unit_amount,
            line_amount=u.line_amount,
            product_id=u.product_id,
        )
        for u in payload.lines
    ]
    try:
        await svc.update_lines(tenant_id=tenant_id, bill_id=bill_id, updates=updates)
    except SupplierBillNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, bills_ar.BILL_NOT_FOUND) from None
    except InvalidBillTransition:
        raise HTTPException(status.HTTP_409_CONFLICT, bills_ar.BILL_INVALID_TRANSITION) from None
    await db.commit()
    bill, supplier, lines = await svc.get_with_lines(tenant_id=tenant_id, bill_id=bill_id)
    return await _detail(request, settings, tenant_id, bill, supplier, lines)


@router.post("/bills/{bill_id}/approve", response_model=BillDetail)
async def approve_bill(
    bill_id: UUID,
    payload: ApproveBillRequest,
    request: Request,
    user: CurrentUser,
    db: Db,
    settings: Config,
    background: BackgroundTasks,
) -> BillDetail:
    """Approve a bill, then mint a signed `bill.commit` token and fire the gated stock
    commit as a BACKGROUND task — the call returns immediately with status
    `committing`, even if the commit is slow.

    Refuses (422) if any quantitied line is still unmapped (a bill can't commit a line
    with no product). 404 if the bill isn't this tenant's; 409 if it's not under
    review. The token authorizes EXACTLY this (bill.commit, bill_id, tenant,
    approver) — the committer re-verifies it before any stock change (constitution V).
    """
    tenant_id = user.tenant_id
    svc = SupplierBillService(db)

    if not await svc.all_required_lines_mapped(tenant_id=tenant_id, bill_id=bill_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, bills_ar.BILL_LINES_NOT_MAPPED)

    try:
        bill = await svc.approve(tenant_id=tenant_id, bill_id=bill_id, approver_id=user.id)
    except SupplierBillNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, bills_ar.BILL_NOT_FOUND) from None
    except InvalidBillTransition:
        raise HTTPException(status.HTTP_409_CONFLICT, bills_ar.BILL_INVALID_TRANSITION) from None
    await db.commit()

    # The approve has COMMITTED (status `committing`). Mint the signed token now (the
    # gate's "yes", bound to this exact action/resource/tenant/approver) and schedule
    # the commit out of band — never inline, so a slow commit can't hang the call.
    token = mint_approval_token(
        settings,
        action=COMMIT_ACTION,
        resource_id=bill.id,
        tenant_id=tenant_id,
        approver_id=user.id,
    )
    committer = request.app.state.bill_committer
    background.add_task(committer.commit, bill, token)

    bill, supplier, lines = await svc.get_with_lines(tenant_id=tenant_id, bill_id=bill_id)
    return await _detail(request, settings, tenant_id, bill, supplier, lines)


@router.post("/bills/{bill_id}/reject", response_model=BillDetail)
async def reject_bill(
    bill_id: UUID,
    payload: RejectBillRequest,
    request: Request,
    user: CurrentUser,
    db: Db,
    settings: Config,
) -> BillDetail:
    """Reject a bill. reason is required (422 if absent — schema-enforced). The image
    stays in MinIO with the reason recorded; provisions nothing. 404 if it isn't this
    tenant's; 409 if it's not under review."""
    tenant_id = user.tenant_id
    svc = SupplierBillService(db)
    try:
        await svc.reject(
            tenant_id=tenant_id, bill_id=bill_id, approver_id=user.id, reason=payload.reason
        )
    except SupplierBillNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, bills_ar.BILL_NOT_FOUND) from None
    except InvalidBillTransition:
        raise HTTPException(status.HTTP_409_CONFLICT, bills_ar.BILL_INVALID_TRANSITION) from None
    await db.commit()
    bill, supplier, lines = await svc.get_with_lines(tenant_id=tenant_id, bill_id=bill_id)
    return await _detail(request, settings, tenant_id, bill, supplier, lines)
