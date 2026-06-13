"""Task 5.6 — bill image preprocessing + per-field confidence formula.

Proves preprocess() cleans a raw photo (returns a valid grayscale PNG) and that its
deskew recovers a known skew angle, and that the confidence formula combines OCR
confidence with extraction certainty correctly (product, clamped, min over a bill,
threshold flag). Pure functions, no I/O — driven by synthetic fixtures.
"""

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.infra.ocr import confidence as C
from app.infra.ocr.preprocess import _estimate_skew, preprocess


def _ruled_page(width: int = 400, height: int = 300) -> Image.Image:
    """A white page with horizontal black bars standing in for text rows."""
    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)
    for y in range(40, height - 20, 30):
        draw.rectangle([40, y, width - 40, y + 10], fill=0)
    return img


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Preprocessing ────────────────────────────────────────────────────────────


def test_preprocess_returns_grayscale_png() -> None:
    raw = _png_bytes(_ruled_page())
    out = preprocess(raw)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
    with Image.open(io.BytesIO(out)) as img:
        assert img.mode == "L"  # grayscale


def test_preprocess_handles_color_input() -> None:
    """A colour phone photo is accepted and converted to grayscale."""
    color = Image.new("RGB", (300, 200), color=(200, 180, 160))
    out = preprocess(_png_bytes(color))
    with Image.open(io.BytesIO(out)) as img:
        assert img.mode == "L"


def test_deskew_recovers_known_skew() -> None:
    """A page skewed by +6° is estimated as ~-6° (the rotation that levels it)."""
    skewed = _ruled_page().rotate(6, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    angle = _estimate_skew(np.asarray(skewed.convert("L")))
    assert angle == pytest.approx(-6.0, abs=1.0)


def test_level_image_is_not_rotated() -> None:
    """An already-level page estimates ~0° (we don't blur a straight image)."""
    angle = _estimate_skew(np.asarray(_ruled_page()))
    assert angle == pytest.approx(0.0, abs=0.5)


def test_preprocess_raises_on_garbage() -> None:
    """Non-image bytes raise (the worker maps this to ocr_failed)."""
    with pytest.raises(ValueError):
        preprocess(b"not-an-image")


# ── Confidence formula ───────────────────────────────────────────────────────


def test_field_confidence_is_the_product() -> None:
    assert C.field_confidence(0.9, 0.8) == 0.72
    assert C.field_confidence(1.0, 0.5) == 0.5
    # Either source being low drags the field down.
    assert C.field_confidence(0.99, 0.2) == 0.198


def test_field_confidence_clamps_out_of_range() -> None:
    assert C.field_confidence(1.5, 0.5) == 0.5  # >1 clamped to 1
    assert C.field_confidence(0.5, -0.2) == 0.0  # <0 clamped to 0


def test_min_confidence_over_a_bill() -> None:
    assert C.min_confidence([0.9, 0.72, 0.95]) == 0.72
    assert C.min_confidence([]) is None  # no scored fields → null, not 0/1


def test_needs_review_flags_at_or_below_threshold() -> None:
    assert C.needs_review(0.7, 0.75) is True
    assert C.needs_review(0.75, 0.75) is True  # boundary is flagged
    assert C.needs_review(0.8, 0.75) is False
