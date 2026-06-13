"""ASR model card (Phase 12, AD-12.4/AD-12.5). A small JSON describing the fine-tuned
artifact and — unlike the ~1 GB model weights — COMMITTED. Because git cannot track a
file under the git-ignored artifacts/ dir, the committed card lives in app/asr/ (its
exact path is fixed in Task 12.3); a copy may also sit beside the weights locally.

A card makes the saved model self-describing and defensible: what it is, what base
checkpoint it started from, on what dataset/hours/split it was trained and evaluated,
its WER/CER versus the zero-shot baseline, and — crucially — the honesty caveat that
the data is public/MSA-leaning Arabic, NOT Lebanese dialect (AD-12.5). The serving
seam (Task 12.4) can read the card to confirm which artifact it loaded without
unpacking the weights.

Stdlib only (json) — like results.py, it must not pull in the heavy ASR stack.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# The standing honesty caveat (AD-12.5): we prove the PIPELINE on public Arabic and
# report a real WER on it; we do NOT claim Lebanese-dialect accuracy from MSA-leaning
# data. The path to real dialect data is the Phase-12.5 follow-up.
DIALECT_CAVEAT = (
    "Trained and evaluated on Mozilla Common Voice Arabic, which is broad and "
    "MSA-leaning — NOT Lebanese dialect. The reported WER/CER reflect public Arabic "
    "and prove the pipeline is correct; Lebanese-dialect accuracy is unproven until "
    "consented owner/customer voice notes are collected and the same pipeline is "
    "re-run on them (AD-12.5)."
)


@dataclass
class ASRModelCard:
    model: str  # the produced artifact name, e.g. "whisper-small-ar"
    base_model: str  # the HF checkpoint, e.g. "openai/whisper-small"
    language: str  # ISO code, "ar"
    dataset: str  # e.g. "mozilla-foundation/common_voice_17_0 (ar)"
    wer: float  # fine-tuned Word Error Rate on the eval split
    cer: float  # fine-tuned Character Error Rate on the eval split
    train_hours: float
    n_train: int
    n_eval: int
    epochs: float
    # The zero-shot whisper-small baseline on the SAME split — the comparison that
    # makes the fine-tune defensible (AD-12.2/AD-12.7). 0.0 until measured.
    zeroshot_wer: float = 0.0
    split: str = "validation"
    data_source: str = "public_arabic"  # honesty marker (AD-12.5)
    dialect_caveat: str = DIALECT_CAVEAT
    notes: str = ""
    trained_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Trailing newline so the committed card satisfies the end-of-file-fixer
        # pre-commit hook (otherwise every retrain trips it).
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    @staticmethod
    def load(path: Path) -> "ASRModelCard":
        return ASRModelCard(**json.loads(path.read_text(encoding="utf-8")))
