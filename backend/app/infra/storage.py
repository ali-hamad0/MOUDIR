"""Object storage for supplier-bill images (Phase 5, Task 5.1).

This is the ONE place an object enters or leaves MinIO. It mirrors the dev-safe,
Vault-credentialed shape of the other infra clients (`EmailSender`,
`SupplierDispatcher`): credentials resolve from `Settings` (filled from Vault
`modir/minio` at startup), and application code calls `StorageClient`, never the
MinIO SDK directly — swapping the backend (S3, GCS) is a change in THIS module.

THE WALL FOR OBJECT STORAGE. Every key is built through `object_key`, which
prefixes it with the tenant id:

    bills/{tenant_id}/{bill_id}/{safe_filename}

A key is only ever constructed for the caller's tenant, so a presigned URL or a
fetch can only ever reach that tenant's objects — tenant A can never name, let
alone read, tenant B's bill image (constitution I). The tenant prefix is the
storage analogue of every repository method taking `tenant_id`.

The MinIO SDK is synchronous. Calling it directly inside an async route would
block the event loop (constitution: never call a blocking SDK synchronously in an
async path), so every SDK call runs in a worker thread via `asyncio.to_thread`.
The `requests` library is never used (constitution); the MinIO SDK uses urllib3
internally, which is acceptable — application code does not import `requests`.
"""

import asyncio
import io
import re
from datetime import timedelta
from uuid import UUID

from minio import Minio

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)

# Collapse only genuinely unsafe characters — path separators, control chars,
# whitespace, and S3-key metacharacters — to an underscore. Unicode letters and
# digits are KEPT: Lebanese owners' bill filenames are routinely Arabic, and S3/
# MinIO object keys are UTF-8, so "فاتورة المورد.jpg" should stay readable rather
# than collapse to a bare extension. The tenant prefix is added separately and is
# never derived from caller input, so the Wall does not depend on this.
_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f/\\?%*:|\"<>\s]+")


def _safe_filename(filename: str | None) -> str:
    """Sanitize a client-supplied filename for use as the last key segment.

    Drops any path components and unsafe characters while preserving Unicode
    letters/digits (so an Arabic name survives). Falls back to a fixed name when
    nothing usable remains — the key's uniqueness comes from the bill id, not the
    filename, so an empty or odd filename is harmless.
    """
    if not filename:
        return "bill"
    # Drop any directory components a client might have sent ("../../etc").
    base = filename.replace("\\", "/").split("/")[-1]
    cleaned = _UNSAFE_FILENAME.sub("_", base).strip("._")
    return cleaned or "bill"


class StorageClient:
    """Streams supplier-bill images to/from MinIO under tenant-prefixed keys.

    One instance is built in `lifespan` (like the other infra clients) and reused;
    it holds no per-call state, so it is concurrency-safe. The underlying MinIO
    client is thread-safe; its blocking calls are dispatched to threads so the
    event loop is never blocked.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.minio_bucket
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        )
        # A second client for PRESIGNING only: presigned URLs are signature-bound
        # to the host, so browser-facing URLs must be signed for the public
        # endpoint. The region is pinned because minio-py otherwise fetches the
        # bucket location over the network first — and the public endpoint is
        # NOT reachable from inside the container (that's the whole point of it).
        # With the region set, presigning is pure local computation.
        public = settings.minio_public_endpoint or settings.minio_endpoint
        self._presign_client = Minio(
            public,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
            region="us-east-1",  # MinIO's default region
        )

    @staticmethod
    def object_key(tenant_id: UUID, bill_id: UUID, filename: str | None) -> str:
        """Build the tenant-prefixed object key for a bill image — the Wall.

        Shape: ``bills/{tenant_id}/{bill_id}/{safe_filename}``. The tenant id comes
        first so every object physically sorts and scopes under its tenant, and a
        key is only ever built for the caller's tenant. The filename is sanitized;
        uniqueness comes from the bill id, not the filename.
        """
        return f"bills/{tenant_id}/{bill_id}/{_safe_filename(filename)}"

    async def ensure_bucket(self) -> None:
        """Create the bill bucket if it does not exist (idempotent).

        Called once at startup. Safe to call repeatedly — it checks first and only
        creates when missing, so a restart against an existing bucket is a no-op.
        """

        def _ensure() -> bool:
            if self._client.bucket_exists(self._bucket):
                return False
            self._client.make_bucket(self._bucket)
            return True

        created = await asyncio.to_thread(_ensure)
        log.info("storage.bucket.ready", bucket=self._bucket, created=created)

    async def put_stream(self, key: str, data: bytes, content_type: str | None) -> None:
        """Upload an object's bytes under `key` (already tenant-prefixed).

        Takes the bytes (the upload route reads the multipart body and hands them
        here); MinIO needs a stream + length, so we wrap them in a BytesIO. The key
        must come from `object_key` so the tenant prefix is present.
        """

        def _put() -> None:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type or "application/octet-stream",
            )

        await asyncio.to_thread(_put)
        log.info("storage.put", key=key, size=len(data))

    async def get_bytes(self, key: str) -> bytes:
        """Fetch an object's bytes by `key` (tenant-prefixed).

        Used by the worker to pull a bill image for OCR. The caller is responsible
        for only ever passing a key built for its own tenant (the Wall).
        """

        def _get() -> bytes:
            response = self._client.get_object(self._bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        data = await asyncio.to_thread(_get)
        log.info("storage.get", key=key, size=len(data))
        return data

    async def presigned_get(self, key: str, ttl: timedelta) -> str:
        """A time-boxed URL the review screen uses to show the bill image.

        The URL expires after `ttl` so it cannot be shared or replayed
        indefinitely. The key is tenant-prefixed, so the URL can only ever point at
        the owning tenant's object.
        """
        url = await asyncio.to_thread(
            self._presign_client.presigned_get_object, self._bucket, key, expires=ttl
        )
        log.info("storage.presigned_get", key=key, ttl_seconds=int(ttl.total_seconds()))
        return url
