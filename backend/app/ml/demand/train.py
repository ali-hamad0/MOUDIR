"""Demand forecaster training (Phase 6, Task 6.5). ONE command retrains it from scratch:

    cd backend
    DATABASE_URL=... REDIS_URL=... VAULT_ADDR=... VAULT_TOKEN=... \
    uv run python -m app.ml.train_demand

What it does (Constitution IV, line by line):
  - loads the leakage-free demand features for every trainable tenant (app.ml.dataset);
  - builds a sklearn.Pipeline per candidate with preprocessing INSIDE the pipeline (no
    leakage across the CV boundary);
  - compares >=3 candidates (Ridge baseline / HistGradientBoosting / XGBoost) with
    TimeSeriesSplit (NOT a random shuffle — demand is temporal, AD-6.6) and logs every
    run to results.csv with CV mean +- std;
  - refits the winner (lowest CV MAE) on all data and saves it to artifacts/demand.joblib
    plus a model_card.json (features, window, data_source).

Brand-new product with NO history: it never reaches a row here (the feature builder needs
prior days), and at serve time the predictor returns None so the caller falls back to its
documented default (AD-6.5) — the model is never asked to hallucinate a cold-start number.
"""

import argparse
import asyncio
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from xgboost import XGBRegressor

from app.db.session import create_engine
from app.infra.logging import configure_logging, get_logger
from app.ml.dataset import load_demand_dataset
from app.ml.features.demand import feature_columns
from app.ml.model_card import ModelCard
from app.ml.predictors import ARTIFACTS_DIR
from app.ml.results import ExperimentResult, log_result

log = get_logger("train_demand")

TASK = "demand"
TARGET = "units"
METRIC = "MAE"
N_SPLITS = 5
ARTIFACT = ARTIFACTS_DIR / "demand.joblib"
CARD = ARTIFACTS_DIR / "demand_card.json"


def _candidates(features: list[str]) -> dict[str, Pipeline]:
    """The >=3 candidate pipelines. Preprocessing lives INSIDE each Pipeline so a CV
    fold's scaler is fit on the train split only — no leakage across folds."""
    # All inputs are numeric here. Early days of a product's history have NaN lags
    # (no prior week/fortnight yet); impute INSIDE the pipeline so the fill value is
    # learned on the train fold only (no leakage). Median is robust to demand spikes.
    # Ridge also needs scaling; the trees take the imputed values straight through.
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
        "ridge": Pipeline([("pre", scaled), ("model", Ridge(alpha=1.0))]),
        "hgbr": Pipeline(
            [("pre", imputed), ("model", HistGradientBoostingRegressor(random_state=42))]
        ),
        "xgboost": Pipeline(
            [
                ("pre", imputed),
                (
                    "model",
                    XGBRegressor(
                        n_estimators=300,
                        max_depth=6,
                        learning_rate=0.05,
                        random_state=42,
                        n_jobs=2,
                    ),
                ),
            ]
        ),
    }


def _cv_mae(pipeline: Pipeline, X: pd.DataFrame, y: pd.Series) -> tuple[float, float]:
    """TimeSeriesSplit CV MAE (mean, std). The frame MUST already be sorted by day so
    each fold trains on earlier days and tests on later ones (honest temporal CV)."""
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    scores: list[float] = []
    for train_idx, test_idx in tscv.split(X):
        pipeline.fit(X.iloc[train_idx], y.iloc[train_idx])
        pred = pipeline.predict(X.iloc[test_idx])
        scores.append(mean_absolute_error(y.iloc[test_idx], pred))
    return float(np.mean(scores)), float(np.std(scores))


def train_from_frame(
    df: pd.DataFrame,
    *,
    n_tenants: int,
    artifact: Path = ARTIFACT,
    card_path: Path = CARD,
    results_path: Path | None = None,
) -> ModelCard | None:
    """Compare candidates on an already-loaded feature frame, log each to results.csv,
    save the winner + card. Returns the card (or None if there's nothing to train on).

    Output paths are parameters (defaulting to the committed artifact locations) so a
    test can redirect them to a tmp dir and a Colab run can write elsewhere — separated
    from the DB loading so it is unit-testable with a synthetic frame."""
    if df.empty:
        log.warning("train_demand.no_data")
        return None

    # Temporal order is the backbone of honest CV: sort by day before splitting.
    df = df.sort_values("day").reset_index(drop=True)
    features = feature_columns()
    X = df[features]
    y = df[TARGET]

    results: dict[str, tuple[float, float]] = {}
    for name, pipeline in _candidates(features).items():
        mean, std = _cv_mae(pipeline, X, y)
        results[name] = (mean, std)
        result = ExperimentResult(
            task=TASK,
            model=name,
            metric=METRIC,
            cv_mean=mean,
            cv_std=std,
            n_splits=N_SPLITS,
            data_source="synthetic",
        )
        log_result(result) if results_path is None else log_result(result, path=results_path)
        log.info("train_demand.candidate", model=name, cv_mae=round(mean, 3), cv_std=round(std, 3))

    # Winner = lowest CV MAE. Refit on ALL rows, then persist.
    winner = min(results, key=lambda k: results[k][0])
    best_mean, best_std = results[winner]
    pipeline = _candidates(features)[winner]
    pipeline.fit(X, y)

    artifact.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, artifact)
    card = ModelCard(
        task=TASK,
        model=winner,
        metric=METRIC,
        cv_mean=best_mean,
        cv_std=best_std,
        n_splits=N_SPLITS,
        features=features,
        target=TARGET,
        n_rows=len(df),
        n_tenants=n_tenants,
        data_source="synthetic",
        notes="Per-product daily demand. TimeSeriesSplit CV; lower MAE wins. Cold-start "
        "products have no history and fall back to the caller's default at serve time.",
    )
    card.save(card_path)
    log.info(
        "train_demand.saved", winner=winner, cv_mae=round(best_mean, 3), artifact=str(artifact)
    )
    return card


async def train() -> ModelCard | None:
    """Load the dataset from Postgres and train. The one-command entrypoint calls this."""
    engine = create_engine()
    try:
        sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        df, n_tenants = await load_demand_dataset(sessionmaker)
        return train_from_frame(df, n_tenants=n_tenants)
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging()
    argparse.ArgumentParser(description="Train the demand forecaster (Phase 6).").parse_args()
    asyncio.run(train())


if __name__ == "__main__":
    main()
