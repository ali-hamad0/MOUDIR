"""Text-to-speech via Gemini native TTS (Phase 10 — voice chat).

The reverse of audio_transcriber.py: takes a Lebanese Arabic reply string and
returns playable audio bytes for the dashboard's push-to-talk chat.

Mode selection mirrors every other provider-agnostic seam in Modir (and uses
the same switch as the transcriber, whatsapp_mode — the audio seam mode):
  "dev"  — returns a tiny silent WAV (no network, no key — CI/offline default).
  "live" — POSTs to the Gemini TTS generateContent REST endpoint via httpx.

Gemini TTS returns raw 16-bit PCM (mono, 24 kHz) base64-encoded; browsers can't
play bare PCM, so we wrap it in a minimal WAV container here. Audio content is
never logged (only its size).
"""

from __future__ import annotations

import base64
import re
import struct

import httpx

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)

_GEMINI_TTS_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)
_TTS_MODEL = "gemini-2.5-flash-preview-tts"
# A prebuilt multilingual Gemini voice; renders Arabic text naturally.
_TTS_VOICE = "Kore"

_PCM_RATE_RE = re.compile(r"rate=(\d+)")


def wrap_pcm_in_wav(pcm: bytes, *, sample_rate: int = 24000) -> bytes:
    """Wrap raw 16-bit mono PCM in a minimal WAV container (RIFF header)."""
    channels, bits = 1, 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,  # PCM fmt chunk size
        1,  # audio format: PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        len(pcm),
    )
    return header + pcm


# 0.1s of silence — a valid, playable WAV for the dev stub.
_DEV_STUB_WAV = wrap_pcm_in_wav(b"\x00\x00" * 2400)


class SpeechSynthesizer:
    """Turn a Lebanese Arabic reply into playable WAV bytes."""

    def __init__(self, settings: Settings) -> None:
        self._mode = settings.whatsapp_mode
        # SecretStr — only unwrapped at call time
        self._settings = settings

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return (wav_bytes, mime_type) for the given text.

        In dev mode the text is ignored and a short silent WAV is returned.
        """
        log.info("tts.synthesize.start", mode=self._mode, text_length=len(text))

        if self._mode != "live":
            log.info("tts.synthesize.dev_stub")
            return _DEV_STUB_WAV, "audio/wav"

        api_key = self._settings.gemini_api_key.get_secret_value()
        url = _GEMINI_TTS_URL.format(model=_TTS_MODEL, api_key=api_key)
        body = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": _TTS_VOICE}}},
            },
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=body)

        if response.status_code != 200:
            log.error("tts.synthesize.api_error", status=response.status_code)
            response.raise_for_status()

        data = response.json()
        try:
            part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm = base64.b64decode(part["data"])
            mime: str = part.get("mimeType", "audio/L16;rate=24000")
        except (KeyError, IndexError) as exc:
            log.error("tts.synthesize.parse_error", error=str(exc))
            raise

        rate_match = _PCM_RATE_RE.search(mime)
        sample_rate = int(rate_match.group(1)) if rate_match else 24000
        wav = wrap_pcm_in_wav(pcm, sample_rate=sample_rate)
        log.info("tts.synthesize.done", bytes=len(wav), sample_rate=sample_rate)
        return wav, "audio/wav"


def build_speech_synthesizer(settings: Settings) -> SpeechSynthesizer:
    """Factory called once in the lifespan handler (mirrors build_audio_transcriber)."""
    synthesizer = SpeechSynthesizer(settings)
    log.info("tts.synthesizer.ready", mode=settings.whatsapp_mode)
    return synthesizer
