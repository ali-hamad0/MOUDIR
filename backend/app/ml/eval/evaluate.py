"""Golden evaluation (Phase 6, Task 6.11). Run the committed golden sets through the REAL
trained artifacts and check each clears its threshold (thresholds.yaml):

    cd backend && uv run python -m app.ml.eval

Exits non-zero if any present model fails — so a model broken on purpose turns CI red. A
model whose artifact is ABSENT is SKIPPED (clearly logged), never a failure: CI without the
committed artifact simply reports "skipped", mirroring the stub-by-default contract.

The evaluator touches no DB and no Settings — it loads artifacts + golden JSON from disk —
so the CI step needs no services.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import pandas as pd
import yaml
from sklearn.metrics import mean_absolute_error, precision_score, recall_score

from app.infra.logging import get_logger
from app.ml.features.churn import feature_columns as churn_features
from app.ml.features.demand import feature_columns as demand_features

log = get_logger("ml.eval")

EVAL_DIR = Path(__file__).parent
GOLDEN_DIR = EVAL_DIR / "golden"
THRESHOLDS = EVAL_DIR / "thresholds.yaml"
# The committed artifacts live one level up (app/ml/artifacts). Resolved from disk so the
# evaluator needs no Settings/env — CI can run it without DB or Vault.
ARTIFACTS_DIR = EVAL_DIR.parent / "artifacts"


@dataclass
class EvalResult:
    model: str
    passed: bool
    skipped: bool = False
    metrics: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    note: str = ""


def load_thresholds(path: Path = THRESHOLDS) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_golden(name: str) -> list[dict]:
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


def evaluate_demand(
    thresholds: dict,
    *,
    artifact: Path = ARTIFACTS_DIR / "demand.joblib",
    golden: list[dict] | None = None,
) -> EvalResult:
    if not artifact.exists():
        return EvalResult("demand", passed=True, skipped=True, note="artifact absent")
    df = pd.DataFrame(golden if golden is not None else _load_golden("demand"))
    model = joblib.load(artifact)
    mae = float(mean_absolute_error(df["units"], model.predict(df[demand_features()])))
    th = thresholds["demand"]
    return EvalResult(
        "demand", passed=mae <= th["mae_max"], metrics={"mae": round(mae, 3)}, thresholds=th
    )


def evaluate_churn(
    thresholds: dict,
    *,
    artifact: Path = ARTIFACTS_DIR / "churn.joblib",
    golden: list[dict] | None = None,
) -> EvalResult:
    if not artifact.exists():
        return EvalResult("churn", passed=True, skipped=True, note="artifact absent")
    df = pd.DataFrame(golden if golden is not None else _load_golden("churn"))
    model = joblib.load(artifact)
    pred = model.predict(df[churn_features()])
    recall = float(recall_score(df["churned"], pred, pos_label=1, zero_division=0))
    precision = float(precision_score(df["churned"], pred, pos_label=1, zero_division=0))
    th = thresholds["churn"]
    passed = recall >= th["recall_pos_min"] and precision >= th["precision_pos_min"]
    return EvalResult(
        "churn",
        passed=passed,
        metrics={"recall_pos": round(recall, 3), "precision_pos": round(precision, 3)},
        thresholds=th,
    )


def evaluate_anomaly(
    thresholds: dict,
    *,
    artifact: Path = ARTIFACTS_DIR / "anomaly.joblib",
    golden: list[dict] | None = None,
) -> EvalResult:
    if not artifact.exists():
        return EvalResult("anomaly", passed=True, skipped=True, note="artifact absent")
    df = pd.DataFrame(golden if golden is not None else _load_golden("anomaly"))
    detector = joblib.load(artifact)
    flags = detector.flag(df).astype(int)
    recall = float(recall_score(df["is_anomaly"], flags, pos_label=1, zero_division=0))
    precision = float(precision_score(df["is_anomaly"], flags, pos_label=1, zero_division=0))
    th = thresholds["anomaly"]
    passed = recall >= th["recall_pos_min"] and precision >= th["precision_pos_min"]
    return EvalResult(
        "anomaly",
        passed=passed,
        metrics={"recall_pos": round(recall, 3), "precision_pos": round(precision, 3)},
        thresholds=th,
    )


def run(thresholds: dict | None = None) -> list[EvalResult]:
    thresholds = thresholds if thresholds is not None else load_thresholds()
    return [
        evaluate_demand(thresholds),
        evaluate_churn(thresholds),
        evaluate_anomaly(thresholds),
    ]


def main() -> int:
    results = run()
    failed = False
    for r in results:
        status = "SKIP" if r.skipped else ("PASS" if r.passed else "FAIL")
        log.info(
            "ml.eval.result",
            model=r.model,
            status=status,
            metrics=r.metrics,
            thresholds=r.thresholds,
            note=r.note,
        )
        failed = failed or (not r.passed and not r.skipped)
    if failed:
        log.error("ml.eval.failed", note="a model regressed below its golden threshold")
        return 1
    log.info("ml.eval.ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
