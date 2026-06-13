"""The ASR experiment log (Constitution IV / AD-12.7: eval is the grade).

Every transcription run — the zero-shot whisper-small baseline AND the fine-tuned
model — appends one row here, so the WER/CER comparison is a committed record, not a
claim (the Phase-12 mirror of app/ml/results.py). The file is append-only and its
header is committed (see results.csv), so a fresh checkout already has the schema and
CI can diff it.

This module is deliberately tiny and dependency-free (stdlib csv): it is imported by
app.asr at package init and by the training entrypoint (Task 12.3), so it must NOT
drag in torch/transformers/datasets just to write a row — that heavy stack stays
quarantined to the functions that actually train/serve (AD-12.6).
"""

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.infra.logging import get_logger

log = get_logger(__name__)

# The experiment log lives beside this module, inside the app.asr package, so it ships
# with the code and a training run from any CWD writes to the same canonical file.
RESULTS_PATH = Path(__file__).parent / "results.csv"

# The committed schema. Adding a column is a deliberate change (update the header in
# results.csv too). Kept explicit so every run logs the SAME shape.
FIELDNAMES = [
    "timestamp",  # ISO-8601 UTC, when the run was logged
    "model",  # the candidate, e.g. "whisper-small-zeroshot" | "whisper-small-ft"
    "base_model",  # the HF checkpoint the run started from, e.g. "openai/whisper-small"
    "dataset",  # e.g. "common_voice_ar"
    "split",  # which split the WER/CER was measured on, e.g. "validation" | "test"
    "hours",  # hours of audio (train hours for a fine-tune; eval hours for a baseline)
    "n_examples",  # number of clips in the measured split
    "wer",  # Word Error Rate on the split (the primary metric)
    "cer",  # Character Error Rate on the split (secondary)
    "data_source",  # "public_arabic" — honesty marker (NOT Lebanese dialect; AD-12.5)
    "notes",  # free-form (epochs, lr, caveats)
]


@dataclass
class ASRExperimentResult:
    """One row of results.csv — one model's WER/CER on one split."""

    model: str
    dataset: str
    wer: float
    cer: float
    split: str = "validation"
    hours: float = 0.0
    n_examples: int = 0
    base_model: str = "openai/whisper-small"
    data_source: str = "public_arabic"
    notes: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_row(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "model": self.model,
            "base_model": self.base_model,
            "dataset": self.dataset,
            "split": self.split,
            "hours": self.hours,
            "n_examples": self.n_examples,
            "wer": self.wer,
            "cer": self.cer,
            "data_source": self.data_source,
            "notes": self.notes,
        }


def ensure_results_file(path: Path = RESULTS_PATH) -> None:
    """Create results.csv with the header if it does not exist (idempotent)."""
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()


def log_result(result: ASRExperimentResult, path: Path = RESULTS_PATH) -> None:
    """Append one experiment row. Creates the file with a header first if needed.

    Append-only by design: the log is the audit trail of every run, so we never
    rewrite or truncate it.
    """
    ensure_results_file(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(result.as_row())
    log.info(
        "asr.experiment.logged",
        model=result.model,
        dataset=result.dataset,
        split=result.split,
        wer=result.wer,
        cer=result.cer,
    )
