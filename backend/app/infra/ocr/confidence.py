"""Per-field confidence scoring for OCR'd bills (Phase 5, Task 5.6).

A bill's trustworthiness has two independent sources of doubt:

  1. **OCR confidence** — did the engine read the pixels correctly? (per-block, from
     the OCREngine, Task 5.5)
  2. **Extraction certainty** — given the text, did the extraction step parse this
     field correctly? (per-field, from the BillExtractionAgent, Task 5.7)

A field is only as trustworthy as the weaker of the two, so the combined score is
their PRODUCT (both must be high to trust a field). The result is a 0..1 score per
field/line; `min_confidence` over a bill is the page-level review signal stored on
the row (Task 5.3 model). A score at or below `ocr_confidence_review_threshold` is
FLAGGED for the human's attention in the review UI — it is NOT an auto-commit gate
(every bill goes to a human in Phase 5).

Pure functions, no I/O — the worker (Task 5.8) and the extraction step share them so
scores are computed one way everywhere.
"""


def field_confidence(ocr_confidence: float, extraction_certainty: float) -> float:
    """Combine OCR confidence and extraction certainty into one 0..1 field score.

    The product: a field is trustworthy only if BOTH the pixels were read well AND
    the value was parsed confidently. Either being low drags the score down. Inputs
    are clamped to [0, 1] defensively so an out-of-range provider value can't produce
    a nonsense score.
    """
    a = _clamp01(ocr_confidence)
    b = _clamp01(extraction_certainty)
    return round(a * b, 3)


def min_confidence(scores: list[float]) -> float | None:
    """The lowest field score on a bill — the page-level review signal.

    None when there are no scored fields (an empty/failed extraction), so the row's
    `min_confidence` stays null rather than a misleading 0 or 1.
    """
    if not scores:
        return None
    return round(min(_clamp01(s) for s in scores), 3)


def needs_review(score: float, threshold: float) -> bool:
    """True if `score` is at or below `threshold` — flag it for the human.

    A SIGNAL for the UI, not a gate: a flagged field still goes through the same
    human review; nothing auto-commits in Phase 5.
    """
    return _clamp01(score) <= threshold


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))
