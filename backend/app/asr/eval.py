"""WER/CER evaluation of a fine-tuned artifact (Task 12.6).

Computes Word/Character Error Rate of the loaded model against a split or the committed
golden set, and is the basis for the CI floor (`eval_thresholds.yaml: asr.wer_max`).
When no artifact is present (the CI/dev default), the eval is skipped/marked clearly,
never failed — exactly the Phase-6 golden-eval discipline.

SCAFFOLD (Task 12.1): contract fixed; torch/transformers/evaluate are imported LAZILY
inside the functions (AD-12.6), so `import app.asr.eval` works with none installed.
The eval body and the golden set land in Task 12.6.
"""

from app.infra.logging import get_logger

log = get_logger(__name__)


def evaluate_artifact(model_path: str, *, split: str = "validation") -> dict[str, float]:
    """Return {"wer": ..., "cer": ...} for the artifact at `model_path` on `split`.

    Lazy imports of the heavy stack happen HERE (AD-12.6). Filled in Task 12.6.
    """
    raise NotImplementedError("WER/CER evaluation is implemented in Task 12.6.")
