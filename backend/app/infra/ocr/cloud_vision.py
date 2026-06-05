"""Google Cloud Vision OCR engine (Phase 5) — the real Lebanese-Arabic engine.

THIS is the only module that imports the Google Cloud Vision SDK (the constitution's
provider-agnostic boundary — CI guards that nothing else imports it). The credential
is the GCP service-account JSON resolved from Vault (`Settings.ocr_service_account_json`,
seeded at `modir/ocr`); it is never read from env or a literal.

Cloud Vision's client is synchronous and makes a blocking network call, so `extract`
runs it in a worker thread via `asyncio.to_thread` (constitution: never block the
event loop). The library uses gRPC/httpx internally — application code never imports
`requests`.

Because Docker DNS is blocked in this environment, this engine is verified on the
HOST venv (with a real service account), not in-container; CI/tests use the stub.
"""

import asyncio
import json

from app.infra.logging import get_logger
from app.infra.ocr.engine import OCRBlock, OCRResult
from app.infra.settings import Settings

log = get_logger(__name__)

# document_text_detection is tuned for dense documents (a supplier bill), unlike
# text_detection which targets sparse text in photos. Arabic is a supported script.
_ENGINE_NAME = "cloud_vision"


class CloudVisionOCREngine:
    """OCR via Google Cloud Vision `document_text_detection`.

    Built once (lifespan / the worker builder) with the Vault-resolved credential.
    The Vision client is created lazily on first use and reused; it is thread-safe,
    and the blocking call is dispatched to a thread so the event loop is free.
    """

    engine_name = _ENGINE_NAME

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None  # built lazily — see _get_client

    def _get_client(self):
        """Build (once) the Vision client from the Vault service-account JSON.

        Imports the SDK here so it is needed only when this engine is actually used.
        A missing/blank credential is a configuration error surfaced clearly rather
        than a confusing SDK auth failure deep in a call.
        """
        if self._client is not None:
            return self._client

        # Imported inside the method so merely importing this module (e.g. for a
        # type) does not require the heavy SDK; the factory already gates on mode.
        from google.cloud import vision
        from google.oauth2 import service_account

        raw = self._settings.ocr_service_account_json.get_secret_value()
        if not raw or raw.strip() in ("", "{}"):
            raise RuntimeError(
                "ocr_mode=cloud_vision but no GCP service-account JSON is configured "
                "(Vault modir/ocr.service_account_json is empty)"
            )
        info = json.loads(raw)
        credentials = service_account.Credentials.from_service_account_info(info)
        self._client = vision.ImageAnnotatorClient(credentials=credentials)
        return self._client

    async def extract(self, image: bytes) -> OCRResult:
        """Recognize text in `image` via Cloud Vision, off the event loop.

        Returns the full text plus a per-block (paragraph) confidence breakdown.
        Raises RuntimeError on a Vision API error response so the worker records the
        bill `ocr_failed` (Task 5.8)."""
        result = await asyncio.to_thread(self._detect, image)
        log.info("ocr.cloud_vision.extract", blocks=len(result.blocks))
        return result

    def _detect(self, image: bytes) -> OCRResult:
        """The blocking Vision call + response mapping (runs in a thread)."""
        from google.cloud import vision

        client = self._get_client()
        response = client.document_text_detection(image=vision.Image(content=image))
        if response.error.message:
            # Vision reports failures in the response body, not as an exception.
            raise RuntimeError(f"cloud vision error: {response.error.message}")

        annotation = response.full_text_annotation
        full_text = annotation.text if annotation else ""
        blocks: list[OCRBlock] = []
        if annotation:
            # Map each paragraph to a block, joining its word symbols into text and
            # carrying the paragraph confidence Vision reports (0..1).
            for page in annotation.pages:
                for block in page.blocks:
                    for paragraph in block.paragraphs:
                        words = [
                            "".join(symbol.text for symbol in word.symbols)
                            for word in paragraph.words
                        ]
                        text = " ".join(w for w in words if w)
                        if text:
                            blocks.append(
                                OCRBlock(text=text, confidence=float(paragraph.confidence))
                            )
        return OCRResult(text=full_text, engine=self.engine_name, blocks=blocks)
