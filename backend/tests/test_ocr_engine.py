"""Task 5.5 — the OCR engine seam.

Proves the provider-agnostic OCREngine seam: the stub returns deterministic text +
per-block confidence (offline, the CI/dev path), the factory selects the engine from
ocr_mode, the reserved tesseract mode is a clear NotImplementedError, and the
cloud_vision engine surfaces a clear config error when no GCP credential is set
(without touching the network). The provider SDK stays confined to
app/infra/ocr/cloud_vision.py — asserted here too.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from app.infra.ocr import StubOCREngine, build_ocr_engine
from app.infra.ocr.engine import OCRBlock, OCRResult
from app.infra.settings import Settings


async def test_stub_extract_is_deterministic_with_blocks() -> None:
    engine = StubOCREngine()
    r1 = await engine.extract(b"any-bytes")
    r2 = await engine.extract(b"different-bytes")  # bytes ignored by design

    assert r1.engine == "stub"
    assert r1.text == r2.text  # deterministic, independent of input
    assert len(r1.blocks) > 0
    assert all(0.0 <= b.confidence <= 1.0 for b in r1.blocks)
    # The stub looks like a real Lebanese bill so extraction has something to chew.
    assert "فاتورة" in r1.text


async def test_stub_min_confidence() -> None:
    engine = StubOCREngine(confidence=0.8)
    result = await engine.extract(b"x")
    assert result.min_confidence == pytest.approx(0.8)


def test_ocr_result_min_confidence_none_without_blocks() -> None:
    assert OCRResult(text="", engine="stub", blocks=[]).min_confidence is None
    assert (
        OCRResult(
            text="a", engine="stub", blocks=[OCRBlock("a", 0.5), OCRBlock("b", 0.9)]
        ).min_confidence
        == 0.5
    )


def test_factory_selects_stub_by_default() -> None:
    engine = build_ocr_engine(Settings.model_construct(ocr_mode="stub"))
    assert isinstance(engine, StubOCREngine)


def test_factory_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        build_ocr_engine(Settings.model_construct(ocr_mode="banana"))


def test_factory_tesseract_is_reserved() -> None:
    with pytest.raises(NotImplementedError):
        build_ocr_engine(Settings.model_construct(ocr_mode="tesseract"))


def test_factory_builds_cloud_vision_without_network() -> None:
    """The cloud_vision factory builds the engine lazily (no call out at build
    time)."""
    from app.infra.ocr.cloud_vision import CloudVisionOCREngine

    engine = build_ocr_engine(
        Settings.model_construct(ocr_mode="cloud_vision", ocr_service_account_json=SecretStr("{}"))
    )
    assert isinstance(engine, CloudVisionOCREngine)


async def test_cloud_vision_blank_credential_is_clear_error() -> None:
    """A cloud_vision engine with no service-account JSON raises a clear config error
    (not a confusing SDK auth failure) and never reaches the network."""
    from app.infra.ocr.cloud_vision import CloudVisionOCREngine

    engine = CloudVisionOCREngine(
        Settings.model_construct(ocr_mode="cloud_vision", ocr_service_account_json=SecretStr(""))
    )
    with pytest.raises(RuntimeError, match="service-account"):
        await engine.extract(b"image")


def test_provider_sdk_is_confined_to_cloud_vision_module() -> None:
    """The constitution boundary: the Google Vision SDK is imported ONLY in
    app/infra/ocr/cloud_vision.py — neither the Protocol/stub module nor the factory
    may import it (so the stub/CI path never needs the heavy SDK)."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        if path.name == "cloud_vision.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "google.cloud" in text or "from google.oauth2" in text:
            offenders.append(str(path.relative_to(app_dir)))
    assert offenders == [], f"GCP SDK leaked outside cloud_vision.py: {offenders}"
