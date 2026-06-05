"""OCR engine seam (Phase 5). Pixels → text + per-block confidence.

Provider-agnostic, mirroring EmailSender / SupplierDispatcher / LLMRouter: callers
depend on the `OCREngine` Protocol, never on a concrete provider. The Google Cloud
Vision SDK is imported ONLY in `cloud_vision.py` (CI guards that boundary).
"""

from app.infra.ocr.engine import (
    OCRBlock,
    OCREngine,
    OCRResult,
    StubOCREngine,
    build_ocr_engine,
)
from app.infra.ocr.preprocess import preprocess

__all__ = [
    "OCRBlock",
    "OCREngine",
    "OCRResult",
    "StubOCREngine",
    "build_ocr_engine",
    "preprocess",
]
