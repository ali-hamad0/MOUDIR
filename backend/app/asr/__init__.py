"""Lebanese Arabic ASR (Phase 12) — a fine-tuned Whisper served behind the existing
AudioTranscriber seam.

This package is the CODE-FIRST source of truth for the fine-tune pipeline (AD-12.1):
dataset prep, feature extraction, training, evaluation, and the model card all live
here as version-controlled `.py`. The Colab notebook only RUNS them on a GPU; no
training logic lives in the notebook.

torch / transformers / datasets are QUARANTINED (AD-12.6): they live in the optional
`asr` extra (pyproject) and are imported LAZILY, inside the functions that need them —
never at any module top in this package. That is precisely why `import app.asr`
succeeds on the default CI/dev path with NONE of the heavy stack installed: this init
surfaces only the stdlib-backed helpers (the experiment log and the model card).
"""

from app.asr.model_card import ASRModelCard
from app.asr.results import ASRExperimentResult, log_result

__all__ = ["ASRExperimentResult", "ASRModelCard", "log_result"]
