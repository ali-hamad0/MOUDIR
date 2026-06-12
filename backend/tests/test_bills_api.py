"""Task 5.4 — supplier-bill upload + review-list API.

Proves the upload streams to MinIO under a tenant-prefixed key and records the bill
`uploaded` (OCR deferred to the worker — nothing runs inline), that bad uploads are
rejected (wrong type, too big, empty), and that the review list is tenant-scoped
(the Wall).

Following the suite's harness, route handlers are called directly with the
transactional db_session and a real User — the routes have no tenant_id parameter to
spoof; scope comes from the user. The Request (for app.state.storage) and the
UploadFile are faked so we inspect what was stored WITHOUT a real MinIO.
"""

import io
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.api.bills import list_bills, upload_bill
from app.db.models import AuditLog, SupplierBill, Tenant, User
from app.infra.security import hash_password
from app.infra.settings import Settings
from app.services.supplier_bills import SupplierBillService


def _settings() -> Settings:
    """Minimal settings with the upload limits + image-URL TTL the routes read."""
    return Settings.model_construct(
        bill_upload_max_bytes=10 * 1024 * 1024,
        bill_upload_allowed_content_types=["image/jpeg", "image/png"],
        bill_image_url_ttl_minutes=15,
    )


class _SpyStorage:
    """Captures put_stream(key, data, content_type) instead of hitting MinIO."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str | None]] = []

    async def put_stream(self, key: str, data: bytes, content_type: str | None) -> None:
        self.puts.append((key, data, content_type))

    async def presigned_get(self, key: str, ttl) -> str:
        return f"https://signed.example/{key}"


def _fake_request(storage: _SpyStorage):
    """A stand-in Request exposing only app.state.storage."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(storage=storage)))


def _upload(
    *, filename: str = "فاتورة.jpg", content_type: str = "image/jpeg", data: bytes = b"\xff\xd8jpeg"
) -> UploadFile:
    """A fake multipart UploadFile with the given bytes + content type."""
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


@dataclass
class _Seed:
    tenant_id: UUID
    user_id: UUID


async def _seed(db: AsyncSession, *, name: str = "ShopA") -> _Seed:
    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"{uuid4().hex[:8]}@a.com",
        hashed_password=hash_password("password123"),
        role="owner",
    )
    db.add(user)
    await db.flush()
    return _Seed(tenant_id=tenant.id, user_id=user.id)


async def _user(db: AsyncSession, seed: _Seed) -> User:
    return (await db.execute(select(User).where(User.id == seed.user_id))).scalar_one()


# ── Upload happy path ────────────────────────────────────────────────────────


async def test_upload_streams_to_storage_and_records_uploaded(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    user = await _user(db_session, seed)
    storage = _SpyStorage()

    accepted = await upload_bill(
        request=_fake_request(storage),
        user=user,
        db=db_session,
        settings=_settings(),
        file=_upload(data=b"\xff\xd8imagebytes"),
    )

    assert accepted.status == "uploaded"
    # The image was streamed to MinIO under a tenant-prefixed key (the Wall).
    assert len(storage.puts) == 1
    key, data, content_type = storage.puts[0]
    assert key.startswith(f"bills/{seed.tenant_id}/{accepted.id}/")
    assert data == b"\xff\xd8imagebytes"
    assert content_type == "image/jpeg"

    # The bill row was persisted in `uploaded` with that exact key + id.
    bill = (
        await db_session.execute(select(SupplierBill).where(SupplierBill.id == accepted.id))
    ).scalar_one()
    assert bill.status == "uploaded"
    assert bill.object_key == key
    assert bill.tenant_id == seed.tenant_id

    # And it was audited as bill.uploaded.
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "bill.uploaded", AuditLog.target == str(accepted.id))
        )
    ).scalar_one()
    assert n == 1


async def test_upload_does_not_run_ocr_inline(db_session: AsyncSession) -> None:
    """The bill must be left `uploaded` (the worker OCRs later); the upload never
    advances it to extracted/ocr_processing in-request."""
    seed = await _seed(db_session)
    user = await _user(db_session, seed)
    accepted = await upload_bill(
        request=_fake_request(_SpyStorage()),
        user=user,
        db=db_session,
        settings=_settings(),
        file=_upload(),
    )
    bill = (
        await db_session.execute(select(SupplierBill).where(SupplierBill.id == accepted.id))
    ).scalar_one()
    assert bill.status == "uploaded"
    assert bill.ocr_text is None and bill.extracted is None


# ── Upload validation ────────────────────────────────────────────────────────


async def test_upload_rejects_unsupported_type(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    user = await _user(db_session, seed)
    storage = _SpyStorage()
    with pytest.raises(HTTPException) as ei:
        await upload_bill(
            request=_fake_request(storage),
            user=user,
            db=db_session,
            settings=_settings(),
            file=_upload(content_type="application/pdf", filename="bill.pdf"),
        )
    assert ei.value.status_code == 415
    assert storage.puts == []  # nothing stored


async def test_upload_rejects_oversize(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    user = await _user(db_session, seed)
    storage = _SpyStorage()
    settings = Settings.model_construct(
        bill_upload_max_bytes=1024,  # 1 KiB cap
        bill_upload_allowed_content_types=["image/jpeg"],
    )
    big = b"x" * 2048  # 2 KiB > cap
    with pytest.raises(HTTPException) as ei:
        await upload_bill(
            request=_fake_request(storage),
            user=user,
            db=db_session,
            settings=settings,
            file=_upload(data=big),
        )
    assert ei.value.status_code == 413
    assert storage.puts == []


async def test_upload_rejects_empty(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    user = await _user(db_session, seed)
    storage = _SpyStorage()
    with pytest.raises(HTTPException) as ei:
        await upload_bill(
            request=_fake_request(storage),
            user=user,
            db=db_session,
            settings=_settings(),
            file=_upload(data=b""),
        )
    assert ei.value.status_code == 400
    assert storage.puts == []


# ── Review list: tenant-scoped (the Wall) ────────────────────────────────────


async def test_list_bills_shows_extracted_and_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    a_user = await _user(db_session, a)

    # An extracted bill for A and one for B.
    async def _extracted(seed: _Seed) -> UUID:
        svc = SupplierBillService(db_session)
        bill = await svc.create_uploaded(
            tenant_id=seed.tenant_id,
            object_key=f"bills/{seed.tenant_id}/{uuid4()}/x.jpg",
            original_filename="x.jpg",
            content_type="image/jpeg",
        )
        await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=bill.id)
        await svc.save_extraction(
            tenant_id=seed.tenant_id,
            bill_id=bill.id,
            ocr_engine="stub",
            ocr_text="...",
            extracted={},
            lines=[],
            min_confidence=None,
        )
        return bill.id

    a_bill = await _extracted(a)
    b_bill = await _extracted(b)

    page = await list_bills(
        request=_fake_request(_SpyStorage()),
        user=a_user,
        db=db_session,
        settings=_settings(),
        limit=50,
        offset=0,
    )
    ids = {item.id for item in page.items}
    assert a_bill in ids
    assert b_bill not in ids  # the Wall: B's bill never shows for A
    assert page.total == 1
    # Each item carries a presigned thumbnail URL of the scan (Phase 10), built
    # from the tenant-prefixed key — the list shows the photo, not just metadata.
    assert all(item.image_url and f"bills/{a.tenant_id}/" in item.image_url for item in page.items)
