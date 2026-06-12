"""The OCR engine Protocol + the dev/test stub + the engine factory.

OCR is the pixels → text step (constitution IV: the OCR engine reads the image; the
LLM only STRUCTURES the resulting text — Task 5.7). An `OCREngine` returns the full
text plus per-block text+confidence, so the extraction step and the UI can flag the
low-confidence regions of a bill.

Three implementations, selected by `Settings.ocr_mode` (like mail_mode /
po_dispatch_mode):
- `StubOCREngine`  — deterministic canned output for CI/tests and the dev default
  when no GCP credential is configured. Offline, no network, no key.
- `CloudVisionOCREngine` (in `cloud_vision.py`) — Google Cloud Vision, the real
  Lebanese-Arabic engine. Its GCP credential resolves from Vault; verified on the
  HOST (Docker DNS is blocked in-container).
- Tesseract — reserved as a documented offline fallback; not built yet.

The provider SDK is confined to `cloud_vision.py`; this module imports it lazily in
the factory so the stub path (CI) never needs `google-cloud-vision` installed.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)


@dataclass(frozen=True)
class OCRBlock:
    """One recognized region of text with its confidence (0..1).

    A bill OCRs into many blocks (lines / paragraphs); keeping per-block confidence
    lets the extraction step and the review UI flag exactly which parts of the bill
    are uncertain (Task 5.6/5.12), rather than trusting the whole page equally.
    """

    text: str
    confidence: float


@dataclass(frozen=True)
class OCRResult:
    """The output of an OCR pass: the full text plus the per-block breakdown.

    `text` is the whole document as one string (what the extraction LLM reads).
    `blocks` carries the per-region confidence used to compute the per-field scores.
    `engine` records which engine produced it (stub | cloud_vision | tesseract) so a
    bill is reproducible/auditable.
    """

    text: str
    engine: str
    blocks: list[OCRBlock] = field(default_factory=list)

    @property
    def min_confidence(self) -> float | None:
        """The lowest block confidence, or None when there are no blocks. A quick
        page-level signal; the per-field formula (Task 5.6) refines it."""
        if not self.blocks:
            return None
        return min(b.confidence for b in self.blocks)


class OCREngine(Protocol):
    """The only way app code turns image bytes into text. Callers depend on this
    Protocol, never on a concrete provider — swapping or adding an engine is a
    config change (`ocr_mode`) behind the factory, never a change in the worker."""

    async def extract(self, image: bytes) -> OCRResult:
        """Recognize text in `image` (already preprocessed bytes), returning the full
        text + per-block confidence. Raises on a hard engine failure (the worker maps
        that to `ocr_failed`, Task 5.8)."""
        ...


# A fixed, deterministic Lebanese-Arabic supplier-bill text for tests + dev. It
# looks like a real bill (supplier line, a few item lines with quantities/amounts, a
# total) so the extraction step (Task 5.7) has something realistic to structure,
# while staying completely offline.
_STUB_TEXT = "\n".join(
    [
        "مؤسسة الأمين للمواد الغذائية",
        "فاتورة رقم ١٢٣",
        "طحين ٥٠ كيلو ١٠٠٠٠٠٠ ل.ل",
        "سكر ٢٥ كيلو ٧٥٠٠٠٠ ل.ل",
        "زيت ١٢ علبة ٩٠٠٠٠٠ ل.ل",
        "المجموع ٢٦٥٠٠٠٠ ل.ل",
    ]
)


class StubOCREngine:
    """Deterministic OCR for CI/tests and the dev default (offline, no key).

    Returns a fixed Lebanese-Arabic bill text with high, fixed per-block confidence.
    The extraction tests (Task 5.9) drive a realistic page WITHOUT any network or GCP
    credential — the same discipline as the mocked LLM in the existing suites.
    """

    engine_name = "stub"

    def __init__(self, *, text: str = _STUB_TEXT, confidence: float = 0.95) -> None:
        self._text = text
        self._confidence = confidence

    async def extract(self, image: bytes) -> OCRResult:  # noqa: ARG002 — bytes ignored by design
        blocks = [
            OCRBlock(text=line, confidence=self._confidence)
            for line in self._text.splitlines()
            if line
        ]
        log.info("ocr.stub.extract", blocks=len(blocks))
        return OCRResult(text=self._text, engine=self.engine_name, blocks=blocks)


def build_ocr_engine(settings: Settings) -> OCREngine:
    """Construct the OCR engine for the configured `ocr_mode` (built once in
    lifespan / the worker builder).

    The Cloud Vision implementation (and its SDK) is imported lazily HERE so the stub
    path — CI, tests, dev without a GCP key — never needs `google-cloud-vision`
    installed and never touches the network.
    """
    mode = settings.ocr_mode
    if mode == "cloud_vision":
        # Lazy import keeps the provider SDK out of the stub/CI path (and confined to
        # cloud_vision.py — the constitution's provider-agnostic boundary).
        from app.infra.ocr.cloud_vision import CloudVisionOCREngine

        log.info("ocr.engine.selected", mode=mode)
        return CloudVisionOCREngine(settings)
    if mode == "gemini":
        # Real OCR with the EXISTING Gemini key (no GCP service account needed) —
        # Phase 10. Lazy import mirrors the cloud_vision branch.
        from app.infra.ocr.gemini_vision import GeminiOCREngine

        log.info("ocr.engine.selected", mode=mode)
        return GeminiOCREngine(settings)
    if mode == "tesseract":
        raise NotImplementedError(
            "ocr_mode='tesseract' is reserved as a documented offline fallback; "
            "not implemented yet (use 'stub' or 'cloud_vision')"
        )
    if mode != "stub":
        raise ValueError(f"unknown ocr_mode: {mode!r}")
    log.info("ocr.engine.selected", mode=mode)
    return StubOCREngine()
