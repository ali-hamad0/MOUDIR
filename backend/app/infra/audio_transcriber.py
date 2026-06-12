"""Voice-message transcription via Gemini native audio (Phase 10, Task 10.5).

Receives raw OGG bytes downloaded from Meta's media API and returns the
Lebanese Arabic transcription as a plain string.

Mode selection mirrors every other provider-agnostic seam in Modir:
  "dev"  — returns the canned Arabic stub from prompts/audio_ar.py (no network,
            no key — the CI/offline default, controlled by whatsapp_mode).
  "live" — POSTs to the Gemini generateContent REST endpoint via httpx with
            the audio bytes base64-encoded as inline data.

This is the ONE place where we call the Gemini REST API directly instead of
going through the LangChain/LLM router — LangChain's ChatGoogleGenerativeAI
wrapper does not support inline audio bytes reliably. The deviation is
intentional and documented in PHASE_10.md.
"""

from __future__ import annotations

import base64

import httpx

from app.infra.logging import get_logger
from app.infra.settings import Settings
from prompts import audio_ar

log = get_logger(__name__)

_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)
# gemini-1.5-flash was retired from the v1beta API (404s since late 2025).
_TRANSCRIPTION_MODEL = "gemini-2.5-flash"


class AudioTranscriber:
    """Transcribe OGG voice messages to Lebanese Arabic text."""

    def __init__(self, settings: Settings) -> None:
        self._mode = settings.whatsapp_mode
        # SecretStr — only unwrapped at call time
        self._settings = settings

    async def transcribe(
        self, audio_bytes: bytes, mime_type: str = "audio/ogg; codecs=opus"
    ) -> str:
        """Return the Lebanese Arabic transcription of the audio.

        In dev mode the audio bytes are ignored and the stub is returned.
        In live mode the bytes are base64-encoded and sent to Gemini inline.
        Audio content is never logged (only its size).
        """
        log.info("audio.transcribe.start", mode=self._mode, bytes=len(audio_bytes))

        if self._mode != "live":
            log.info("audio.transcribe.dev_stub")
            return audio_ar.DEV_STUB

        api_key = self._settings.gemini_api_key.get_secret_value()
        audio_b64 = base64.b64encode(audio_bytes).decode()

        url = _GEMINI_GENERATE_URL.format(model=_TRANSCRIPTION_MODEL, api_key=api_key)
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": audio_ar.TRANSCRIPTION_SYSTEM},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": audio_b64,
                            }
                        },
                    ]
                }
            ]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=body)

        if response.status_code != 200:
            log.error(
                "audio.transcribe.api_error",
                status=response.status_code,
            )
            response.raise_for_status()

        data = response.json()
        try:
            text: str = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as exc:
            log.error("audio.transcribe.parse_error", error=str(exc))
            return audio_ar.DEV_STUB

        log.info("audio.transcribe.done", transcript_length=len(text))
        return text


def build_audio_transcriber(settings: Settings) -> AudioTranscriber:
    """Factory called once in the lifespan handler.

    Mirrors build_whatsapp_client / build_ocr_engine — named factory so tests
    can patch without importing the class directly.
    """
    transcriber = AudioTranscriber(settings)
    log.info("audio.transcriber.ready", mode=settings.whatsapp_mode)
    return transcriber
