"""WhatsApp Business API client (Phase 10, Task 10.3).

Mirrors the provider-agnostic seam used by EmailSender, SupplierDispatcher,
and the OCR engine: whatsapp_mode controls whether we call the real Meta API
or just log the payload.

  "dev"  — structured log only; never calls graph.facebook.com. CI default.
  "live" — POSTs to the Meta Cloud API and downloads media from it.

The api_token is a SecretStr resolved from Vault at startup; it is extracted
via get_secret_value() only at the point of use, never earlier, and never
logged.
"""

from __future__ import annotations

import httpx

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)

_META_API_BASE = "https://graph.facebook.com/v19.0"
_MEDIA_URL = "https://graph.facebook.com/v19.0/{media_id}"


class WhatsAppClient:
    """Send text replies and download voice-message media via the Meta API."""

    def __init__(self, settings: Settings) -> None:
        self._mode = settings.whatsapp_mode
        self._phone_number_id = settings.whatsapp_phone_number_id
        # SecretStr — only unwrapped at call time, never stored as plain string
        self._settings = settings

    async def send_text(self, to: str, body: str) -> None:
        """Send a text message to a WhatsApp number.

        In dev mode: logs the payload (safe for CI and local runs).
        In live mode: POSTs to Meta Cloud API.
        The bearer token is extracted from settings at call time.
        """
        if self._mode != "live":
            log.info(
                "whatsapp.send_text.dev",
                to=to,
                body_length=len(body),
            )
            return

        token = self._settings.whatsapp_api_token.get_secret_value()
        url = f"{_META_API_BASE}/{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),  # Meta expects numbers without leading +
            "type": "text",
            "text": {"body": body},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code not in (200, 201):
            # Meta's error body carries the actionable reason (e.g. 131030
            # "recipient not in allowed list" on a sandbox number, or an
            # expired token) — without it a bare 400 is undiagnosable.
            log.error(
                "whatsapp.send_text.failed",
                status=response.status_code,
                phone_number_id=self._phone_number_id,
                meta_error=response.text[:500],
            )
            response.raise_for_status()
        log.info("whatsapp.send_text.sent", to=to, body_length=len(body))

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Download a media file (e.g. voice OGG) from the Meta media API.

        Returns (bytes, mime_type). In dev mode returns a small silent OGG stub
        so the audio path can be exercised without a real Meta token.

        The media_id is logged as a length-8 prefix only (never the full value).
        """
        log.info("whatsapp.download_media", media_id_prefix=media_id[:8])

        if self._mode != "live":
            # Minimal valid OGG header (4 bytes) — enough for dev-mode tests
            return b"OggS", "audio/ogg; codecs=opus"

        token = self._settings.whatsapp_api_token.get_secret_value()
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: resolve the media URL from the media_id
            meta_resp = await client.get(
                _MEDIA_URL.format(media_id=media_id),
                headers={"Authorization": f"Bearer {token}"},
            )
            meta_resp.raise_for_status()
            data = meta_resp.json()
            media_url: str = data["url"]
            mime_type: str = data.get("mime_type", "audio/ogg; codecs=opus")

            # Step 2: download the actual binary from the resolved URL
            file_resp = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            file_resp.raise_for_status()

        log.info(
            "whatsapp.download_media.done",
            media_id_prefix=media_id[:8],
            bytes=len(file_resp.content),
            mime_type=mime_type,
        )
        return file_resp.content, mime_type


def build_whatsapp_client(settings: Settings) -> WhatsAppClient:
    """Factory called once in the lifespan handler.

    Mirrors build_ocr_engine / build_embedding_client — a named factory
    so tests can patch it without importing the class directly.
    """
    client = WhatsAppClient(settings)
    log.info("whatsapp.client.ready", mode=settings.whatsapp_mode)
    return client
