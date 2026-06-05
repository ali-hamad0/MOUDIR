"""Supplier-bill upload + review-list API (Phase 5, Task 5.4).

The owner photographs a paper supplier bill from the dashboard. `POST /bills`
streams the image straight to MinIO under a tenant-prefixed key and records the
bill as `uploaded`, returning 202 immediately — OCR is NEVER run in the request
(it takes seconds; the worker, Task 5.8, does it). `GET /bills` is the tenant-scoped
review list.

Every route is tenant-scoped via `get_current_user` (tenant_id from the JWT, never
the path/body — the Wall, constitution I). The MinIO object key is built server-side
from the tenant + a freshly minted bill id; it is never accepted from the client, so
an upload can only ever land under the owning tenant's prefix.
"""

from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
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
from app.api.schemas.bills import BillsPage, BillUploadAccepted
from app.db.models import User
from app.db.session import get_db_session
from app.infra.logging import get_logger
from app.infra.settings import Settings, get_settings
from app.infra.storage import StorageClient
from app.services.supplier_bills import SupplierBillService

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
    user: CurrentUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BillsPage:
    """This tenant's review list: bills awaiting a human (`extracted`) plus the ones
    that failed OCR (`ocr_failed`), joined to supplier, newest first. Scoped to the
    JWT tenant — never shows another tenant's bills (the Wall)."""
    total, items = await SupplierBillService(db).list_for_review(
        tenant_id=user.tenant_id, limit=limit, offset=offset
    )
    return BillsPage(items=items, total=total, limit=limit, offset=offset)
