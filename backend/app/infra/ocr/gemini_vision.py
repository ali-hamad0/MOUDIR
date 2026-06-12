"""Gemini multimodal OCR engine (Phase 10) — receipts via the existing Gemini key.

An alternative real engine behind the same OCREngine Protocol as Cloud Vision,
selected with `ocr_mode="gemini"`. It needs NO extra credential: it reuses the
Vault-resolved `gemini_api_key` that already powers the LLM router, the voice
transcriber, and TTS — so a dev/staging stack can OCR real photos without a GCP
service account.

Like the audio transcriber, this calls the Gemini REST endpoint directly with
the image bytes inline (the documented deviation from the LangChain router for
inline media). Image content is never logged (only its size).

Confidence: Gemini does not report per-block OCR confidence the way Cloud
Vision does, so every block carries a fixed, documented 0.85 — high enough to
trust generally, below 1.0 so the per-field confidence formula (Task 5.6) still
reflects extraction uncertainty. Bills needing certainty-grade per-region
flagging should use `ocr_mode="cloud_vision"`.
"""

from __future__ import annotations

import base64

import httpx

from app.infra.logging import get_logger
from app.infra.ocr.engine import OCRBlock, OCRResult
from app.infra.settings import Settings
from prompts import ocr_ar

log = get_logger(__name__)

_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)
_OCR_MODEL = "gemini-2.5-flash"
_ENGINE_NAME = "gemini"
_BLOCK_CONFIDENCE = 0.85  # fixed — see module docstring


class GeminiOCREngine:
    """OCR via Gemini multimodal generateContent (image inline, text out)."""

    engine_name = _ENGINE_NAME

    def __init__(self, settings: Settings) -> None:
        # SecretStr — only unwrapped at call time
        self._settings = settings

    async def extract(self, image: bytes) -> OCRResult:
        """Recognize all printed text in the (preprocessed PNG) image bytes.

        Raises on a hard API failure — the worker maps that to `ocr_failed`,
        same contract as Cloud Vision.
        """
        log.info("ocr.gemini.extract.start", bytes=len(image))

        api_key = self._settings.gemini_api_key.get_secret_value()
        url = _GEMINI_GENERATE_URL.format(model=_OCR_MODEL, api_key=api_key)
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": ocr_ar.OCR_SYSTEM},
                        {
                            "inline_data": {
                                # preprocess() always emits PNG (lossless).
                                "mime_type": "image/png",
                                "data": base64.b64encode(image).decode(),
                            }
                        },
                    ]
                }
            ]
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=body)

        if response.status_code != 200:
            log.error("ocr.gemini.api_error", status=response.status_code)
            response.raise_for_status()

        data = response.json()
        try:
            text: str = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as exc:
            log.error("ocr.gemini.parse_error", error=str(exc))
            raise ValueError("Gemini OCR returned no text") from exc

        blocks = [
            OCRBlock(text=line, confidence=_BLOCK_CONFIDENCE)
            for line in text.splitlines()
            if line.strip()
        ]
        log.info("ocr.gemini.extract.done", blocks=len(blocks), text_length=len(text))
        return OCRResult(text=text, engine=self.engine_name, blocks=blocks)
