"""Dataset prep for the Whisper fine-tune — Common Voice Arabic (Task 12.2).

Loads Mozilla Common Voice `ar`, casts audio to 16 kHz mono (Whisper expects exactly
that — a wrong sample rate silently wrecks WER), normalizes/cleans the reference text,
and produces deterministic train/val/test splits. A small exporter writes a Colab-ready
subset so the notebook trains without re-streaming the whole corpus.

SCAFFOLD (Task 12.1): the signatures and the contract are fixed here; the body is
filled in Task 12.2. `datasets` / `soundfile` are imported LAZILY inside the functions
(AD-12.6) so `import app.asr.dataset` works on the default path with neither installed.
"""

from dataclasses import dataclass

# Whisper is trained at 16 kHz mono; every clip is resampled to this before features.
TARGET_SAMPLE_RATE = 16_000
# Honesty marker carried onto every row/card produced from this loader (AD-12.5):
# Common Voice Arabic is broad/MSA-leaning, NOT Lebanese dialect.
DATA_SOURCE = "public_arabic"
DATASET_ID = "mozilla-foundation/common_voice_17_0"


@dataclass(frozen=True)
class SplitSizes:
    """How many clips / hours landed in each split — logged and written to the card."""

    n_train: int
    n_val: int
    n_test: int
    train_hours: float


def load_common_voice_ar(*, smoke: bool = False) -> object:
    """Load Common Voice `ar`, resample to 16 kHz mono, clean references, and split.

    `smoke=True` prepares a tiny cached subset for an offline-ish shape test; the full
    run streams the corpus on Colab. Returns the split dataset dict (Task 12.2).
    """
    # Lazy import — keeps the heavy `datasets`/`soundfile` stack off the default path.
    raise NotImplementedError("Dataset prep is implemented in Task 12.2 (PHASE_12.md).")
