"""The experiment log (Constitution IV: "log every experiment to results.csv").

Every training run — every candidate model, not just the winner — appends one row
here so the 3-classifier comparison and CV mean ± std are a committed record, not a
claim. The file is append-only and the header is committed (see results.csv), so a
fresh checkout already has the schema and CI can diff it.

This module is deliberately tiny and dependency-free (stdlib csv): it is imported by
every training entrypoint (Tasks 6.5/6.8/6.9), so it must not drag in pandas just to
write a row.
"""

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.infra.logging import get_logger

log = get_logger(__name__)

# The experiment log lives beside this module, inside the app.ml package, so it ships
# with the code and a training run from any CWD writes to the same canonical file.
RESULTS_PATH = Path(__file__).parent / "results.csv"

# The committed schema. Adding a column is a deliberate change (update the header in
# results.csv too). Kept explicit so every training task logs the SAME shape.
FIELDNAMES = [
    "timestamp",  # ISO-8601 UTC, when the run was logged
    "task",  # "demand" | "churn" | "anomaly"
    "model",  # the candidate, e.g. "xgboost", "logreg", "ridge"
    "params",  # JSON/string of the hyperparameters tried
    "metric",  # the primary metric name, e.g. "MAE", "f1_pos", "precision"
    "cv_mean",  # cross-validation mean of the primary metric
    "cv_std",  # cross-validation std (the "± std" the constitution asks for)
    "n_splits",  # number of CV folds
    "extra",  # JSON/string for per-class metrics or any secondary numbers
    "data_source",  # "synthetic" | "real" — honesty marker (Task 6.2 caveat)
]


@dataclass
class ExperimentResult:
    """One row of results.csv — one candidate model's CV outcome on one task."""

    task: str
    model: str
    metric: str
    cv_mean: float
    cv_std: float
    n_splits: int
    params: str = ""
    extra: str = ""
    data_source: str = "synthetic"
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_row(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "task": self.task,
            "model": self.model,
            "params": self.params,
            "metric": self.metric,
            "cv_mean": self.cv_mean,
            "cv_std": self.cv_std,
            "n_splits": self.n_splits,
            "extra": self.extra,
            "data_source": self.data_source,
        }


def ensure_results_file(path: Path = RESULTS_PATH) -> None:
    """Create results.csv with the header if it does not exist (idempotent)."""
    if path.exists():
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()


def log_result(result: ExperimentResult, path: Path = RESULTS_PATH) -> None:
    """Append one experiment row. Creates the file with a header first if needed.

    Append-only by design: the log is the audit trail of every run, so we never
    rewrite or truncate it.
    """
    ensure_results_file(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(result.as_row())
    log.info(
        "ml.experiment.logged",
        task=result.task,
        model=result.model,
        metric=result.metric,
        cv_mean=result.cv_mean,
        cv_std=result.cv_std,
    )
