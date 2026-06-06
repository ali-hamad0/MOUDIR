"""Churn classifier training (Phase 6, Task 6.8). ONE command retrains it from scratch:

    cd backend
    DATABASE_URL=... REDIS_URL=... VAULT_ADDR=... VAULT_TOKEN=... \
    uv run python -m app.ml.train_churn

What it does (Constitution IV, line by line):
  - loads the leakage-free churn features+label across several monthly cutoffs for every
    trainable tenant (app.ml.dataset — features strictly before each cutoff, label in its
    forward window, AD-6.6);
  - builds a sklearn.Pipeline per candidate with preprocessing INSIDE (no leakage across
    the CV boundary), each handling the class imbalance: LogisticRegression and
    RandomForest with class_weight="balanced", XGBoost with scale_pos_weight;
  - compares the 3 candidates with StratifiedKFold CV (a shuffle is fine — churn is a
    cross-sectional snapshot, NOT a time series like demand) and logs every run to
    results.csv with the headline metric (F1 on the churned class) mean +- std AND the
    full PER-CLASS precision/recall/F1 in `extra` (macro-only would hide the imbalance);
  - refits the winner (highest churned-class F1) on all data and saves it to
    artifacts/churn.joblib plus a model_card.json (label rule, features, per-class metrics).

The label (documented for the defend-it question + DECISIONS): a customer is "at risk /
churned" at a cutoff if they have >=1 PAST order but place NO order in the next
`horizon_days` (default 30). Customers with no prior order are excluded (nothing to
re-engage). See app/ml/features/churn.py for the exact split.
"""

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_fscore_support
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from xgboost import XGBClassifier

from app.db.session import create_engine
from app.infra.logging import configure_logging, get_logger
from app.ml.dataset import load_churn_dataset
from app.ml.features.churn import feature_columns
from app.ml.model_card import ModelCard
from app.ml.predictors import ARTIFACTS_DIR
from app.ml.results import ExperimentResult, log_result

log = get_logger("train_churn")

TASK = "churn"
TARGET = "churned"
METRIC = "f1_pos"  # F1 on the positive (churned) class — the headline for an imbalanced task
N_SPLITS = 5
ARTIFACT = ARTIFACTS_DIR / "churn.joblib"
CARD = ARTIFACTS_DIR / "churn_card.json"

# Monthly cutoffs inside the seeded window (Jun 2024 – May 2025). Each leaves a 30-day
# forward window for the label and enough past history for the features. Stacking them
# gives the classifier temporal variety (a churner is active early, gone later).
CHURN_AS_OFS: tuple[date, ...] = (
    date(2024, 11, 1),
    date(2024, 12, 1),
    date(2025, 1, 1),
    date(2025, 2, 1),
    date(2025, 3, 1),
    date(2025, 4, 1),
)


def _candidates(features: list[str], *, scale_pos_weight: float) -> dict[str, Pipeline]:
    """The >=3 candidate pipelines, each handling the imbalance. Preprocessing lives
    INSIDE each Pipeline so a CV fold's imputer/scaler is fit on the train split only."""
    scaled = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
                ),
                features,
            )
        ],
        remainder="drop",
    )
    imputed = ColumnTransformer(
        [("num", SimpleImputer(strategy="median"), features)], remainder="drop"
    )
    return {
        "logreg": Pipeline(
            [
                ("pre", scaled),
                (
                    "model",
                    LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42),
                ),
            ]
        ),
        "random_forest": Pipeline(
            [
                ("pre", imputed),
                (
                    "model",
                    # Regularized (bounded depth, fewer trees): plenty for 5 features /
                    # ~1k rows, and keeps the committed artifact small (AD-6.4) — an
                    # unbounded 300-tree forest serializes to multiple MB for no gain.
                    RandomForestClassifier(
                        n_estimators=100,
                        max_depth=6,
                        class_weight="balanced",
                        random_state=42,
                        n_jobs=2,
                    ),
                ),
            ]
        ),
        "xgboost": Pipeline(
            [
                ("pre", imputed),
                (
                    "model",
                    XGBClassifier(
                        n_estimators=300,
                        max_depth=4,
                        learning_rate=0.05,
                        scale_pos_weight=scale_pos_weight,
                        random_state=42,
                        n_jobs=2,
                        eval_metric="logloss",
                    ),
                ),
            ]
        ),
    }


def _cv_per_class(
    pipeline: Pipeline, X: pd.DataFrame, y: pd.Series, *, n_splits: int
) -> tuple[float, float, dict]:
    """StratifiedKFold CV. Returns (mean F1 on the churned class, its std, per-class dict).

    Per-class precision/recall/F1 (for BOTH classes, averaged over folds) is reported so
    the imbalance is visible — a macro/accuracy number alone would hide a model that just
    predicts 'not churned' for everyone."""
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    f1_pos: list[float] = []
    agg = {
        0: {"precision": [], "recall": [], "f1": []},
        1: {"precision": [], "recall": [], "f1": []},
    }
    for train_idx, test_idx in skf.split(X, y):
        pipeline.fit(X.iloc[train_idx], y.iloc[train_idx])
        pred = pipeline.predict(X.iloc[test_idx])
        precision, recall, f1, _ = precision_recall_fscore_support(
            y.iloc[test_idx], pred, labels=[0, 1], zero_division=0
        )
        for i, cls in enumerate((0, 1)):
            agg[cls]["precision"].append(precision[i])
            agg[cls]["recall"].append(recall[i])
            agg[cls]["f1"].append(f1[i])
        f1_pos.append(f1[1])
    per_class = {
        str(cls): {m: round(float(np.mean(vals)), 4) for m, vals in metrics.items()}
        for cls, metrics in agg.items()
    }
    return float(np.mean(f1_pos)), float(np.std(f1_pos)), per_class


def train_from_frame(
    df: pd.DataFrame,
    *,
    n_tenants: int,
    artifact: Path = ARTIFACT,
    card_path: Path = CARD,
    results_path: Path | None = None,
) -> ModelCard | None:
    """Compare candidates on an already-loaded churn frame, log each (with per-class
    metrics) to results.csv, save the winner + card. Returns the card (or None if there is
    nothing trainable — empty, single-class, or a minority class too small to stratify).

    Pure (frame in, artifact out) so a test feeds a synthetic imbalanced frame and
    redirects the paths — the real results.csv and committed churn.joblib are untouched."""
    if df.empty or df[TARGET].nunique() < 2:
        log.warning("train_churn.no_data_or_single_class", rows=len(df))
        return None
    min_class = int(df[TARGET].value_counts().min())
    if min_class < 2:
        log.warning("train_churn.minority_too_small", min_class=min_class)
        return None
    n_splits = min(N_SPLITS, min_class)

    features = feature_columns()
    X = df[features]
    y = df[TARGET]
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    scale_pos_weight = n_neg / n_pos if n_pos else 1.0

    results: dict[str, tuple[float, float, dict]] = {}
    for name, pipeline in _candidates(features, scale_pos_weight=scale_pos_weight).items():
        mean, std, per_class = _cv_per_class(pipeline, X, y, n_splits=n_splits)
        results[name] = (mean, std, per_class)
        result = ExperimentResult(
            task=TASK,
            model=name,
            metric=METRIC,
            cv_mean=mean,
            cv_std=std,
            n_splits=n_splits,
            extra=json.dumps(per_class),
            data_source="synthetic",
        )
        log_result(result) if results_path is None else log_result(result, path=results_path)
        log.info(
            "train_churn.candidate",
            model=name,
            f1_pos=round(mean, 3),
            f1_pos_std=round(std, 3),
            recall_pos=per_class["1"]["recall"],
        )

    # Winner = highest churned-class F1 (higher is better, unlike demand's MAE).
    winner = max(results, key=lambda k: results[k][0])
    best_mean, best_std, best_per_class = results[winner]
    pipeline = _candidates(features, scale_pos_weight=scale_pos_weight)[winner]
    pipeline.fit(X, y)

    artifact.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, artifact)
    card = ModelCard(
        task=TASK,
        model=winner,
        metric=METRIC,
        cv_mean=best_mean,
        cv_std=best_std,
        n_splits=n_splits,
        features=features,
        target=TARGET,
        n_rows=len(df),
        n_tenants=n_tenants,
        data_source="synthetic",
        extra={"per_class": best_per_class, "n_pos": n_pos, "n_neg": n_neg},
        notes="At risk = a customer with >=1 past order who places NO order in the next 30 "
        "days. StratifiedKFold CV; highest churned-class F1 wins. Imbalance handled via "
        "class_weight='balanced' (logreg/forest) / scale_pos_weight (xgboost).",
    )
    card.save(card_path)
    log.info(
        "train_churn.saved",
        winner=winner,
        f1_pos=round(best_mean, 3),
        recall_pos=best_per_class["1"]["recall"],
        precision_pos=best_per_class["1"]["precision"],
        artifact=str(artifact),
    )
    return card


async def train() -> ModelCard | None:
    """Load the dataset from Postgres and train. The one-command entrypoint calls this."""
    engine = create_engine()
    try:
        sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        df = await load_churn_dataset(sessionmaker, as_ofs=CHURN_AS_OFS)
        async with sessionmaker() as session:
            from app.repositories.training_data import tenants_for_training

            n_tenants = len(await tenants_for_training(session, min_orders=30))
        return train_from_frame(df, n_tenants=n_tenants)
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging()
    argparse.ArgumentParser(description="Train the churn classifier (Phase 6).").parse_args()
    asyncio.run(train())


if __name__ == "__main__":
    main()
