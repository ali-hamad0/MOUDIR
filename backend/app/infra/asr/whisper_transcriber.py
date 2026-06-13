"""WhisperTranscriber — serve the fine-tuned whisper-small behind the AudioTranscriber
seam (Phase 12, Task 12.4).

torch / transformers / soundfile are imported LAZILY inside this module's functions only
(AD-12.6); nothing here loads at module top, so even importing this file does not pull
torch. The model + processor load ONCE (`load_whisper`, called from
`build_audio_transcriber` in lifespan) and are passed into the transcriber — never loaded
per request. `model.generate` is blocking, so it runs in a worker thread and never stalls
the event loop (constitution: no blocking SDK call inside an async path).

Privacy: audio bytes are never logged — only their size and the resulting transcript
length (constitution III).
"""

from __future__ import annotations

import asyncio
import io

from app.infra.logging import get_logger

log = get_logger(__name__)

# Whisper consumes 16 kHz mono; the serve path resamples to this before features.
TARGET_SAMPLE_RATE = 16_000


def load_whisper(model_path: str, device: str = "cpu") -> tuple[object, object]:
    """Load the fine-tuned model + processor ONCE (called from the factory / lifespan).

    Lazy heavy imports (AD-12.6). Raises if the artifact is unreadable — the caller maps
    that to the gemini fallback so live voice never crashes.
    """
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    log.info("asr.whisper.load.start", path=model_path, device=device)
    processor = WhisperProcessor.from_pretrained(model_path)
    model = WhisperForConditionalGeneration.from_pretrained(model_path)
    model.to(device)
    model.eval()
    # No autograd at inference — saves memory and time.
    torch.set_grad_enabled(False)
    log.info("asr.whisper.load.done", path=model_path, device=device)
    return model, processor


class WhisperTranscriber:
    """Transcribe OGG/opus (or any soundfile-decodable) voice bytes to Lebanese Arabic
    text with the loaded fine-tuned model."""

    def __init__(self, model: object, processor: object, device: str = "cpu") -> None:
        self._model = model
        self._processor = processor
        self._device = device

    async def transcribe(
        self, audio_bytes: bytes, mime_type: str = "audio/ogg; codecs=opus"
    ) -> str:
        log.info("asr.whisper.transcribe.start", bytes=len(audio_bytes), mime=mime_type)
        text = await asyncio.to_thread(self._transcribe_sync, audio_bytes)
        log.info("asr.whisper.transcribe.done", transcript_length=len(text))
        return text

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        import soundfile as sf
        import torch

        audio, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)  # downmix to mono
        if sample_rate != TARGET_SAMPLE_RATE:
            audio = _resample(audio, sample_rate, TARGET_SAMPLE_RATE)

        features = self._processor.feature_extractor(
            audio, sampling_rate=TARGET_SAMPLE_RATE, return_tensors="pt"
        ).input_features.to(self._device)
        with torch.no_grad():
            generated = self._model.generate(features)
        decoded = self._processor.tokenizer.batch_decode(generated, skip_special_tokens=True)
        return decoded[0].strip() if decoded else ""


def _resample(audio: object, src_sr: int, dst_sr: int) -> object:
    """Resample a mono float32 array to `dst_sr`. Uses `scipy.signal.resample_poly`
    (scipy is already a base dependency via scikit-learn). The sample rate MUST match
    what Whisper expects — a wrong rate silently wrecks accuracy.
    """
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(src_sr, dst_sr)
    return resample_poly(audio, dst_sr // g, src_sr // g).astype("float32")
