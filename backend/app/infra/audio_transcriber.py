"""Voice-message transcription behind the `transcribe_mode` seam (Phase 10 → Phase 12).

Mode selection mirrors `ocr_mode` / `ml_mode`: `build_audio_transcriber(settings)` returns,
by `settings.transcribe_mode`, one of three backends that all implement the SAME
`async transcribe(audio_bytes, mime_type) -> str` — so no webhook/chat caller changes:

  "dev"     — `DevAudioTranscriber`: canned Arabic stub (offline, no network/key). CI default.
  "gemini"  — `GeminiAudioTranscriber`: the Phase-10 Gemini native-audio REST path, preserved
              byte-for-byte. The zero-artifact LIVE fallback.
  "whisper" — `WhisperTranscriber` (app/infra/asr/): the fine-tuned model, torch lazily
              imported and loaded ONCE here from lifespan (AD-12.6). If the artifact is
              absent or fails to load, this degrades to gemini (logged) — live voice never
              crashes (the Phase-12 defend-it answer).

The Gemini REST call is the ONE place we hit generativelanguage.googleapis.com directly
instead of the LangChain/LLM router — LangChain's wrapper does not handle inline audio
bytes reliably. The deviation is intentional and documented in PHASE_10.md.

Privacy: audio bytes are never logged — only size + transcript length (constitution III).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol

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


class AudioTranscriber(Protocol):
    """The transcription seam every backend implements. Callers depend on this Protocol,
    never a concrete backend — swapping dev/gemini/whisper is a `transcribe_mode` config
    change behind the factory, never a change in the webhook/chat handler."""

    async def transcribe(
        self, audio_bytes: bytes, mime_type: str = "audio/ogg; codecs=opus"
    ) -> str: ...


class DevAudioTranscriber:
    """Canned Lebanese-Arabic stub — the offline CI/dev default. Audio bytes are ignored
    (only their size is logged)."""

    async def transcribe(
        self, audio_bytes: bytes, mime_type: str = "audio/ogg; codecs=opus"
    ) -> str:
        log.info("audio.transcribe.dev_stub", bytes=len(audio_bytes))
        return audio_ar.DEV_STUB


class GeminiAudioTranscriber:
    """Gemini native-audio REST path (Phase 10, Task 10.5) — behavior preserved byte-for-
    byte from the original AudioTranscriber.live path."""

    def __init__(self, settings: Settings) -> None:
        # SecretStr — only unwrapped at call time.
        self._settings = settings

    async def transcribe(
        self, audio_bytes: bytes, mime_type: str = "audio/ogg; codecs=opus"
    ) -> str:
        log.info("audio.transcribe.start", mode="gemini", bytes=len(audio_bytes))

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
            log.error("audio.transcribe.api_error", status=response.status_code)
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
    """Factory called once in the lifespan handler. Selects the backend by
    `transcribe_mode`. Mirrors build_ocr_engine — named factory so tests can patch and
    so the heavy whisper backend stays behind a lazy import.
    """
    mode = settings.transcribe_mode
    if mode == "whisper":
        return _build_whisper_or_fallback(settings)
    if mode == "gemini":
        log.info("audio.transcriber.ready", mode=mode)
        return GeminiAudioTranscriber(settings)
    if mode != "dev":
        raise ValueError(f"unknown transcribe_mode: {mode!r}")
    log.info("audio.transcriber.ready", mode=mode)
    return DevAudioTranscriber()


def _build_whisper_or_fallback(settings: Settings) -> AudioTranscriber:
    """Load the fine-tuned Whisper model ONCE. If the artifact is missing, or torch/the
    weights fail to load, fall back to Gemini (logged) so live voice keeps working with
    zero new artifact — the constitution's "degrade, never crash" (cf. ml_mode trained →
    stub). The lazy import keeps torch off every other path (AD-12.6).
    """
    path = Path(settings.whisper_model_path)
    if not path.exists():
        log.warning(
            "audio.transcriber.whisper_artifact_missing",
            path=str(path),
            fallback="gemini",
        )
        return GeminiAudioTranscriber(settings)
    try:
        from app.infra.asr.whisper_transcriber import WhisperTranscriber, load_whisper

        model, processor = load_whisper(str(path), settings.whisper_device)
    except Exception as exc:  # noqa: BLE001 — any load failure degrades to gemini, logged
        log.error(
            "audio.transcriber.whisper_load_failed",
            error=str(exc),
            fallback="gemini",
        )
        return GeminiAudioTranscriber(settings)

    log.info(
        "audio.transcriber.ready",
        mode="whisper",
        path=str(path),
        device=settings.whisper_device,
    )
    return WhisperTranscriber(model, processor, settings.whisper_device)
