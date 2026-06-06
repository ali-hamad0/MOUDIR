"""Model cards (Phase 6, AD-6.4). A small JSON written beside each trained artifact.

A card makes a saved model self-describing and defensible: what it is, when it was
trained, on what window and how many rows, which features it uses, who won the
comparison and with what CV score, and — crucially — that the data was SYNTHETIC (the
Task 6.2 honesty rule). The serving seam (Tasks 6.6/6.8/6.9) reads the feature list from
the card so the predictor and the trainer can never silently disagree on columns.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class ModelCard:
    task: str  # "demand" | "churn" | "anomaly"
    model: str  # the winning estimator, e.g. "xgboost"
    metric: str  # the selection metric, e.g. "MAE"
    cv_mean: float
    cv_std: float
    n_splits: int
    features: list[str]
    target: str
    n_rows: int
    n_tenants: int
    data_source: str = "synthetic"
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
    def load(path: Path) -> "ModelCard":
        return ModelCard(**json.loads(path.read_text(encoding="utf-8")))
