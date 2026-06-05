"""Bill image preprocessing (Phase 5, Task 5.6).

A raw phone photo of a paper bill OCRs poorly: it is skewed, noisy, and unevenly lit
(ROADMAP pitfall). `preprocess` cleans it up before OCR — grayscale, autocontrast,
denoise, and deskew — so the OCR engine (Task 5.5) reads sharper text. It is a pure
function `bytes -> bytes` so it is trivially testable on a fixture and has no I/O.

Built on Pillow + numpy (not OpenCV) to keep the dependency footprint light: the
deskew uses a projection-profile angle search (the classic technique — the rotation
that maximizes the variance of the row-sum profile is the one that lines the text
rows up horizontally), which is enough for the small skews a phone photo has.
"""

import io

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from app.infra.logging import get_logger

log = get_logger(__name__)

# Deskew search bounds. Phone photos are roughly upright; only small skews need
# correcting. A coarse pass over [-LIMIT, +LIMIT] in COARSE_STEP degrees finds the
# neighbourhood, then a fine pass refines it — cheap and robust without OpenCV.
_DESKEW_LIMIT_DEG = 10.0
_DESKEW_COARSE_STEP = 1.0
_DESKEW_FINE_STEP = 0.2
# Below this estimated skew we leave the image alone — rotating by a fraction of a
# degree only blurs it.
_DESKEW_MIN_APPLY_DEG = 0.4


def _score_angle(binary: np.ndarray, angle: float) -> float:
    """Variance of the horizontal projection profile after rotating by `angle`.

    When text rows are horizontal, each row is either mostly ink or mostly paper, so
    the row sums vary a lot (high variance). A skewed image smears ink across rows
    and flattens the profile (low variance). So the best deskew angle is the one that
    MAXIMIZES this score.
    """
    rotated = Image.fromarray(binary).rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0)
    row_sums = np.asarray(rotated, dtype=np.float64).sum(axis=1)
    return float(np.var(row_sums))


def _estimate_skew(gray: Image.Image) -> float:
    """Estimate the skew angle (degrees) via a coarse-then-fine projection search.

    Operates on a downscaled, thresholded copy so the search is fast and driven by
    text shape rather than pixel noise. Returns the angle to rotate BY to level the
    text (0.0 when no meaningful skew is found).
    """
    # Downscale for speed — the skew angle is a global property, full resolution adds
    # cost without accuracy.
    small = gray.copy()
    small.thumbnail((600, 600))
    arr = np.asarray(small, dtype=np.uint8)
    # Threshold to ink/paper: text (dark) → 1, background → 0. Use the mean as a
    # simple global threshold (the image is already contrast-normalized upstream).
    binary = (arr < arr.mean()).astype(np.uint8) * 255

    best_angle, best_score = 0.0, _score_angle(binary, 0.0)
    coarse = np.arange(
        -_DESKEW_LIMIT_DEG, _DESKEW_LIMIT_DEG + _DESKEW_COARSE_STEP, _DESKEW_COARSE_STEP
    )
    for angle in coarse:
        score = _score_angle(binary, float(angle))
        if score > best_score:
            best_angle, best_score = float(angle), score

    fine = np.arange(
        best_angle - _DESKEW_COARSE_STEP, best_angle + _DESKEW_COARSE_STEP, _DESKEW_FINE_STEP
    )
    for angle in fine:
        score = _score_angle(binary, float(angle))
        if score > best_score:
            best_angle, best_score = float(angle), score

    return best_angle


def preprocess(image: bytes) -> bytes:
    """Clean a raw bill photo for OCR: orient, grayscale, contrast, denoise, deskew.

    Returns PNG bytes (lossless — re-compressing to JPEG would add artifacts the OCR
    then has to see through). Pure function, no I/O. On any decode/processing error it
    raises; the worker (Task 5.8) treats a preprocessing failure the same as an OCR
    failure (`ocr_failed`).
    """
    with Image.open(io.BytesIO(image)) as img:
        # Honour the camera's EXIF orientation tag so a sideways phone photo is
        # uprighted before anything else.
        img = ImageOps.exif_transpose(img)
        # Grayscale: OCR works on luminance; colour only adds noise.
        gray = img.convert("L")
        # Normalize contrast (stretch the histogram) so faint thermal-printer ink and
        # uneven lighting even out.
        gray = ImageOps.autocontrast(gray)
        # Median filter knocks out speckle/JPEG noise while keeping edges crisp.
        gray = gray.filter(ImageFilter.MedianFilter(size=3))

        angle = _estimate_skew(gray)
        if abs(angle) >= _DESKEW_MIN_APPLY_DEG:
            # expand=True keeps the whole rotated page; white fill matches paper.
            gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
            log.info("ocr.preprocess.deskew", angle=round(angle, 2))
        else:
            log.info("ocr.preprocess.deskew", angle=0.0)

        out = io.BytesIO()
        gray.save(out, format="PNG")
        return out.getvalue()
