"""Bill image preprocessing (Phase 5, Task 5.6) — OpenCV pipeline.

A raw phone photo of a paper bill OCRs poorly: it is skewed, noisy, and unevenly lit
(ROADMAP pitfall). `preprocess` cleans it up before OCR — grayscale, adaptive contrast
(CLAHE), denoise, and deskew — so the OCR engine (Task 5.5, Google Cloud Vision /
Gemini) reads sharper text. It is a pure function `bytes -> bytes` so it is trivially
testable on a fixture and has no I/O.

Built on OpenCV (cv2) + numpy. The deskew uses a projection-profile angle search (the
classic technique — the rotation that maximizes the variance of the row-sum profile is
the one that lines the text rows up horizontally), which is enough for the small skews a
phone photo has. CLAHE (contrast-limited adaptive histogram equalization) evens out the
uneven lighting and faint thermal-printer ink better than a single global stretch.
"""

import cv2
import numpy as np

from app.infra.logging import get_logger

log = get_logger(__name__)

# Deskew search bounds. Phone photos are roughly upright; only small skews need
# correcting. A coarse pass over [-LIMIT, +LIMIT] in COARSE_STEP degrees finds the
# neighbourhood, then a fine pass refines it — cheap and robust.
_DESKEW_LIMIT_DEG = 10.0
_DESKEW_COARSE_STEP = 1.0
_DESKEW_FINE_STEP = 0.2
# Below this estimated skew we leave the image alone — rotating by a fraction of a
# degree only blurs it.
_DESKEW_MIN_APPLY_DEG = 0.4
# The skew search runs on a downscaled copy — the angle is a global property, so full
# resolution only adds cost without accuracy.
_SKEW_SEARCH_MAX_DIM = 600


def _rotate(image: np.ndarray, angle: float, *, border_value: int) -> np.ndarray:
    """Rotate `image` about its centre by `angle` degrees (CCW), keeping the same size.

    Used by the skew search, where the canvas size is irrelevant and the corners that
    rotate out of frame are filled with `border_value` (0 = paper-as-background here).
    """
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def _rotate_expand(image: np.ndarray, angle: float, *, border_value: int) -> np.ndarray:
    """Rotate by `angle` (CCW) and grow the canvas so no corner is clipped.

    The deskew applied to the final page must keep the whole bill; the new bounding box
    is computed from the rotation matrix and the fill matches paper (255 = white).
    """
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)
    matrix[0, 2] += (new_w - w) / 2.0
    matrix[1, 2] += (new_h - h) / 2.0
    return cv2.warpAffine(
        image,
        matrix,
        (new_w, new_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def _score_angle(binary: np.ndarray, angle: float) -> float:
    """Variance of the horizontal projection profile after rotating by `angle`.

    When text rows are horizontal, each row is either mostly ink or mostly paper, so the
    row sums vary a lot (high variance). A skewed image smears ink across rows and
    flattens the profile (low variance). So the best deskew angle MAXIMIZES this score.
    """
    rotated = _rotate(binary, angle, border_value=0)
    row_sums = rotated.astype(np.float64).sum(axis=1)
    return float(np.var(row_sums))


def _estimate_skew(gray: np.ndarray) -> float:
    """Estimate the skew angle (degrees) via a coarse-then-fine projection search.

    `gray` is a single-channel uint8 array. Operates on a downscaled, Otsu-thresholded
    copy so the search is fast and driven by text shape rather than pixel noise. Returns
    the angle to rotate BY to level the text (0.0 when no meaningful skew is found).
    """
    h, w = gray.shape[:2]
    scale = _SKEW_SEARCH_MAX_DIM / float(max(h, w))
    if scale < 1.0:
        small = cv2.resize(
            gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA
        )
    else:
        small = gray
    # Otsu threshold to ink/paper: text (dark) -> 255, background -> 0.
    _, binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

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
    then has to see through). Pure function, no I/O. Raises ValueError on undecodable
    input; the worker (Task 5.8) treats that the same as an OCR failure (`ocr_failed`).
    """
    buf = np.frombuffer(image, dtype=np.uint8)
    # IMREAD_COLOR decodes to BGR and (on modern OpenCV) honours the EXIF orientation
    # tag, so a sideways phone photo is uprighted before anything else.
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("preprocess: input bytes are not a decodable image")

    # Grayscale: OCR works on luminance; colour only adds noise.
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # CLAHE normalizes contrast locally, so faint thermal-printer ink and uneven lighting
    # even out without blowing out the rest of the page.
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    # Median blur knocks out speckle/JPEG noise while keeping edges crisp.
    gray = cv2.medianBlur(gray, 3)

    angle = _estimate_skew(gray)
    if abs(angle) >= _DESKEW_MIN_APPLY_DEG:
        gray = _rotate_expand(gray, angle, border_value=255)
        log.info("ocr.preprocess.deskew", angle=round(angle, 2))
    else:
        log.info("ocr.preprocess.deskew", angle=0.0)

    ok, png = cv2.imencode(".png", gray)
    if not ok:
        raise ValueError("preprocess: failed to encode output PNG")
    return png.tobytes()
