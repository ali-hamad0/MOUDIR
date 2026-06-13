"""Dataset prep for the Whisper fine-tune — Google FLEURS Arabic (Task 12.2).

Loads FLEURS `ar_eg`, casts audio to 16 kHz mono (Whisper expects EXACTLY that — a wrong
sample rate silently wrecks WER), normalizes the reference text with a fixed, recorded
policy (below), and exposes the predefined train/validation/test splits. A small exporter
writes a Colab-ready subset so the notebook trains without re-streaming the whole corpus.

WHY FLEURS, not Common Voice: the original plan (AD-12.5) named Mozilla Common Voice `ar`,
but Hugging Face dropped script-based dataset loading, and `mozilla-foundation/common_voice_17_0`
is a script dataset that no longer resolves through `load_dataset` on any current `datasets`
version. `google/fleurs` is an OFFICIAL, Parquet-formatted multilingual ASR set (no loading
script, no gating) that loads on every `datasets` version. It is read Modern Standard Arabic
— still PUBLIC Arabic, NOT Lebanese dialect, so the honesty caveat is unchanged.

`datasets` / `soundfile` are imported LAZILY inside the functions (AD-12.6), so
`import app.asr.dataset` works on the default CI/dev path with neither installed. The
ONE piece that is pure logic — `normalize_arabic` — has no heavy dependency and is
unit-tested offline (tests/test_asr_dataset.py), because text cleaning is where WER
quietly goes wrong.

────────────────────────────────────────────────────────────────────────────────────
TEXT-NORMALIZATION POLICY (decision, recorded — the spec left this open; AD-12.5):

Applied IDENTICALLY to the training targets and the eval references, so the reported
WER is honest (the model is scored on the same form it was trained to produce):

  1. Strip Arabic diacritics (tashkeel) and the tatweel/kashida elongation. References
     are inconsistently voweled and Whisper does not emit tashkeel, so scoring them
     would be pure noise.
  2. Remove a fixed punctuation set (Arabic + Latin) that Whisper does not reliably
     emit for Arabic, so it does not inflate WER as spurious substitutions.
  3. Collapse all whitespace to single spaces and trim.

We DELIBERATELY do NOT fold the alef / hamza / ya / ta-marbuta orthographic variants.
That folding is standard for MSA benchmarks, but those distinctions can carry dialect
meaning, and over-normalizing would HIDE real model errors behind a lenient scorer.
Honesty over a flattering number (Constitution IV).

The character sets below are built from explicit Unicode codepoints (ASCII-only source),
because tashkeel are combining marks that are invisible — and corrupt easily — in source.
────────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.infra.logging import get_logger

if TYPE_CHECKING:  # import only for type checkers — never at runtime (keeps it torch-free)
    from datasets import DatasetDict

log = get_logger(__name__)

# Whisper is trained at 16 kHz mono; every clip is resampled to this before features.
TARGET_SAMPLE_RATE = 16_000
# Honesty marker carried onto every row/card produced from this loader (AD-12.5):
# FLEURS Arabic is read MSA, NOT Lebanese dialect.
DATA_SOURCE = "public_arabic"
DATASET_ID = "google/fleurs"
DATASET_CONFIG = "ar_eg"
LANGUAGE = "ar"
# FLEURS stores the transcript under "transcription"; we rename it to "sentence" so the
# rest of the pipeline (features/train/eval) stays dataset-agnostic.
TEXT_COLUMN = "transcription"
SPLITS = ("train", "validation", "test")

# Policy step 1 — strip Arabic tashkeel + tatweel. Built from explicit codepoint ranges:
#   U+0610–U+061A pre-Arabic marks, U+064B–U+065F vowels/sukun/shadda, U+0670 superscript
#   alef, U+06D6–U+06DC + U+06DF–U+06E8 + U+06EA–U+06ED extended Quranic marks, and
#   U+0640 the tatweel/kashida elongation.
_DIACRITIC_RANGES = (
    (0x0610, 0x061A),
    (0x064B, 0x065F),
    (0x0670, 0x0670),
    (0x06D6, 0x06DC),
    (0x06DF, 0x06E8),
    (0x06EA, 0x06ED),
    (0x0640, 0x0640),
)
_DIACRITICS = "".join(chr(cp) for lo, hi in _DIACRITIC_RANGES for cp in range(lo, hi + 1))
_DIACRITICS_RE = re.compile(f"[{re.escape(_DIACRITICS)}]")

# Policy step 2 — punctuation mapped to spaces via str.translate. The ASCII run is a
# literal; the non-ASCII marks are built from codepoints: U+00AB/00BB guillemets,
# U+2026 ellipsis, U+060C/061B/061F Arabic comma/semicolon/question, U+2018/2019/201C/201D
# smart quotes.
_PUNCT_ASCII = ".,;:!?\"'`~^*_=+-/\\(){}[]<>"
_PUNCT_NON_ASCII = "".join(
    chr(cp)
    for cp in (0x00AB, 0x00BB, 0x2026, 0x060C, 0x061B, 0x061F, 0x2018, 0x2019, 0x201C, 0x201D)
)
_PUNCT_TABLE = {ord(ch): " " for ch in _PUNCT_ASCII + _PUNCT_NON_ASCII}
_WS_RE = re.compile(r"\s+")


def normalize_arabic(text: str) -> str:
    """Apply the recorded normalization policy to one reference/hypothesis string.

    Pure, dependency-free, deterministic — used for BOTH training targets and eval
    references so WER is measured on a consistent surface form.
    """
    text = _DIACRITICS_RE.sub("", text)
    text = text.translate(_PUNCT_TABLE)
    text = _WS_RE.sub(" ", text)
    return text.strip()


@dataclass(frozen=True)
class SplitSizes:
    """How many clips / hours landed in each split — logged and written to the card."""

    n_train: int
    n_val: int
    n_test: int
    train_hours: float

    def as_dict(self) -> dict[str, float]:
        return {
            "n_train": self.n_train,
            "n_val": self.n_val,
            "n_test": self.n_test,
            "train_hours": round(self.train_hours, 2),
        }


def _hours(dataset: object) -> float:
    """Sum audio duration (hours) of a split from its decoded 16 kHz arrays."""
    total_samples = 0
    for row in dataset:  # type: ignore[attr-defined]
        total_samples += len(row["audio"]["array"])
    return total_samples / TARGET_SAMPLE_RATE / 3600.0


def load_arabic_asr(
    *,
    smoke: bool = False,
    smoke_size: int = 16,
    token: str | None = None,
) -> tuple[DatasetDict, SplitSizes]:
    """Load FLEURS Arabic (ar_eg), resample to 16 kHz mono, normalize references into the
    `sentence` column, and return (DatasetDict, SplitSizes).

    FLEURS is public Parquet (no script, no gating), so it loads on any `datasets`
    version with no `trust_remote_code`. `smoke=True` slices a tiny subset per split for a
    fast pipeline-shape check; the full run streams everything on Colab. Heavy imports are
    local to this function (AD-12.6).
    """
    from datasets import Audio, DatasetDict, load_dataset

    splits: dict[str, object] = {}
    for split in SPLITS:
        spec = f"{split}[:{smoke_size}]" if smoke else split
        ds = load_dataset(DATASET_ID, DATASET_CONFIG, split=spec, token=token)
        # Decode to 16 kHz mono arrays (Whisper's expected input).
        ds = ds.cast_column("audio", Audio(sampling_rate=TARGET_SAMPLE_RATE))
        # Normalize the transcript into a dataset-agnostic `sentence` column.
        ds = ds.rename_column(TEXT_COLUMN, "sentence")
        ds = ds.map(
            lambda batch: {"sentence": normalize_arabic(batch["sentence"])},
            desc=f"normalize:{split}",
        )
        splits[split] = ds
        log.info("asr.dataset.split_loaded", split=split, n=ds.num_rows, smoke=smoke)

    dataset = DatasetDict(splits)
    sizes = SplitSizes(
        n_train=dataset["train"].num_rows,
        n_val=dataset["validation"].num_rows,
        n_test=dataset["test"].num_rows,
        train_hours=_hours(dataset["train"]),
    )
    log.info("asr.dataset.ready", data_source=DATA_SOURCE, **sizes.as_dict())
    return dataset, sizes


def export_for_colab(dataset: DatasetDict, out_dir: str | Path) -> Path:
    """Persist a prepared subset to disk (git-ignored) so the Colab notebook trains from
    a cached snapshot instead of re-streaming the corpus. Mirrors the Phase-6 export
    discipline — data, not code, stays out of the repo.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dataset.save_to_disk(str(out))
    log.info("asr.dataset.exported", path=str(out))
    return out
