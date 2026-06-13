"""WER/CER golden evaluation of the fine-tuned Whisper artifact (Phase 12, Task 12.6).

    cd backend && uv run python -m app.asr.eval

Loads the committed golden set (`eval_golden.json` — a fixed, small slice of the Common
Voice `ar` validation split, referenced by row index) and the WER floor
(`eval_thresholds.yaml`: `asr.wer_max`), runs the fine-tuned model over those clips, and
exits non-zero if WER exceeds the ceiling — so a model broken on purpose turns CI red.

If the artifact is ABSENT (the CI/dev default — it is git-ignored, AD-12.4), the eval is
SKIPPED, clearly logged, never failed — mirroring the Phase-6 ML golden-eval contract.
The heavy stack (torch/transformers/datasets/evaluate) is imported LAZILY inside
`evaluate_golden`, AFTER the artifact-presence check, so `import app.asr.eval` and the
`python -m app.asr.eval` skip path need no torch.

WER/CER are PERCENT (0..100) — the same scale as results.csv and the model card. Public
Arabic, NOT Lebanese dialect: this gates the PIPELINE, not dialect accuracy (AD-12.5).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.infra.logging import get_logger

log = get_logger("asr.eval")

ASR_DIR = Path(__file__).parent
THRESHOLDS_PATH = ASR_DIR / "eval_thresholds.yaml"
GOLDEN_PATH = ASR_DIR / "eval_golden.json"
# The fine-tuned artifact (git-ignored — AD-12.4). Default matches Settings.whisper_model_path.
DEFAULT_ARTIFACT = ASR_DIR / "artifacts" / "whisper-small-ar"


@dataclass
class ASREvalResult:
    passed: bool
    skipped: bool = False
    metrics: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    note: str = ""


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_golden(path: Path = GOLDEN_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_golden(
    model_path: str | Path,
    *,
    thresholds: dict | None = None,
    golden: dict | None = None,
    token: str | None = None,
) -> ASREvalResult:
    """Score the fine-tuned artifact on the golden slice and check WER <= asr.wer_max.

    Returns a SKIPPED result (passed) when the artifact is absent — never raises on the
    default path. Heavy imports happen here, only once the artifact is confirmed present.
    """
    thresholds = thresholds if thresholds is not None else load_thresholds()
    asr_th = thresholds["asr"]
    model_path = Path(model_path)
    if not model_path.exists():
        return ASREvalResult(passed=True, skipped=True, thresholds=asr_th, note="artifact absent")

    golden = golden if golden is not None else load_golden()

    # Lazy heavy imports (AD-12.6) — reached only when the artifact exists.
    import evaluate as hf_evaluate
    import torch
    from datasets import Audio, load_dataset

    from app.asr.dataset import (
        DATASET_CONFIG,
        TARGET_SAMPLE_RATE,
        TEXT_COLUMN,
        normalize_arabic,
    )
    from app.infra.asr.whisper_transcriber import load_whisper

    model, processor = load_whisper(str(model_path))
    ds = load_dataset(
        golden["dataset"],
        golden.get("config", DATASET_CONFIG),
        split=golden["split"],
        token=token,
    )
    ds = ds.cast_column("audio", Audio(sampling_rate=TARGET_SAMPLE_RATE))
    ds = ds.rename_column(golden.get("text_column", TEXT_COLUMN), "sentence")

    preds: list[str] = []
    refs: list[str] = []
    for i in golden["indices"]:
        row = ds[int(i)]
        features = processor.feature_extractor(
            row["audio"]["array"], sampling_rate=TARGET_SAMPLE_RATE, return_tensors="pt"
        ).input_features
        with torch.no_grad():
            generated = model.generate(features)
        pred = processor.tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        preds.append(normalize_arabic(pred))
        refs.append(normalize_arabic(row["sentence"]))

    wer = 100 * hf_evaluate.load("wer").compute(predictions=preds, references=refs)
    cer = 100 * hf_evaluate.load("cer").compute(predictions=preds, references=refs)
    log.info("asr.eval.scored", wer=round(wer, 2), cer=round(cer, 2), n=len(preds))
    return ASREvalResult(
        passed=wer <= asr_th["wer_max"],
        metrics={"wer": round(wer, 2), "cer": round(cer, 2), "n": len(preds)},
        thresholds=asr_th,
    )


def main() -> int:
    result = evaluate_golden(DEFAULT_ARTIFACT)
    status = "SKIP" if result.skipped else ("PASS" if result.passed else "FAIL")
    log.info(
        "asr.eval.result",
        status=status,
        metrics=result.metrics,
        thresholds=result.thresholds,
        note=result.note,
    )
    if not result.passed and not result.skipped:
        log.error("asr.eval.failed", note="WER exceeded asr.wer_max")
        return 1
    log.info("asr.eval.ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
