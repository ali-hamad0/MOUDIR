"""Revenue-anomaly detector training (Phase 6, Task 6.9). ONE command retrains it:

    cd backend
    DATABASE_URL=... REDIS_URL=... VAULT_ADDR=... VAULT_TOKEN=... \
    uv run python -m app.ml.train_anomaly

What it does (Constitution IV + AD-6.2, the unsupervised case):
  - loads the leakage-free daily-revenue features for every trainable tenant (seasonal
    flags + a trailing baseline computed strictly before each day — app.ml.dataset);
  - fits a seasonal EXPECTATION model (HistGradientBoosting) so expected revenue already
    accounts for Ramadan/summer/weekend/payday — a Ramadan spike is seasonality, NOT an
    anomaly (the constitution's explicit pitfall);
  - compares THREE unsupervised detectors on the scale-free relative residual: a robust
    z-score, an IQR rule, and IsolationForest (AD-6.2);
  - because the data is unlabeled, it SCORES each by injecting known synthetic anomalies
    (huge spikes / collapses) into held-out days over several trials and measuring
    detection F1 (mean +- std), logging every candidate to results.csv with its
    precision/recall in `extra`;
  - saves the winner (highest detection F1) to artifacts/anomaly.joblib + a card recording
    the method, threshold, and what it flags.

The threshold is tunable (Z_THRESHOLD / IQR_WHISKER); the card records the chosen value and
its measured precision/recall so "what does it flag?" is answerable from the artifact.
"""

import argparse
import asyncio
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import create_engine
from app.infra.logging import configure_logging, get_logger
from app.ml.anomaly.model import REGRESSOR_FEATURES, SeasonalAnomalyDetector
from app.ml.dataset import load_anomaly_dataset
from app.ml.model_card import ModelCard
from app.ml.predictors import ARTIFACTS_DIR
from app.ml.results import ExperimentResult, log_result

log = get_logger("train_anomaly")

TASK = "anomaly"
METRIC = "f1"  # detection F1 on injected synthetic anomalies (the data is unlabeled)
ARTIFACT = ARTIFACTS_DIR / "anomaly.joblib"
CARD = ARTIFACTS_DIR / "anomaly_card.json"

# Tunable thresholds (recorded in the card). 3.5 robust-z ≈ the common outlier cutoff;
# 1.5 is the standard IQR whisker.
Z_THRESHOLD = 3.5
IQR_WHISKER = 1.5
N_TRIALS = 5  # injection trials → the mean ± std the constitution asks for
INJECT_FRAC = 0.1  # share of days turned into known anomalies per trial
MIN_ROWS = 60


def _fit_seasonal(df: pd.DataFrame) -> HistGradientBoostingRegressor:
    """Expected revenue from calendar context + trailing level (NOT `deviation`)."""
    model = HistGradientBoostingRegressor(random_state=42)
    model.fit(df[REGRESSOR_FEATURES], df["revenue"])
    return model


def _candidates(df: pd.DataFrame) -> dict[str, SeasonalAnomalyDetector]:
    """The 3 unsupervised detectors, all sharing one fitted seasonal model. Their
    parameters are fit on the CLEAN relative residual; scoring injects anomalies."""
    seasonal = _fit_seasonal(df)
    base = SeasonalAnomalyDetector(seasonal_model=seasonal, method="zscore")
    rel = base.relative_residual(df)

    median = float(np.median(rel))
    mad = float(np.median(np.abs(rel - median)))
    scale = 1.4826 * mad or 1.0
    q1, q3 = (float(x) for x in np.percentile(rel, [25, 75]))
    iqr = q3 - q1

    zscore = SeasonalAnomalyDetector(
        seasonal_model=seasonal, method="zscore", threshold=Z_THRESHOLD, center=median, scale=scale
    )
    iqr_det = SeasonalAnomalyDetector(
        seasonal_model=seasonal,
        method="iqr",
        threshold=IQR_WHISKER,
        lower=q1 - IQR_WHISKER * iqr,
        upper=q3 + IQR_WHISKER * iqr,
    )
    iforest_det = SeasonalAnomalyDetector(seasonal_model=seasonal, method="isolation_forest")
    iforest = IsolationForest(n_estimators=100, random_state=42)
    iforest.fit(iforest_det.isolation_matrix(rel, df))
    iforest_det.iforest = iforest

    return {"zscore": zscore, "iqr": iqr_det, "isolation_forest": iforest_det}


def _inject(df: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, np.ndarray]:
    """Turn a held-out slice of days into KNOWN anomalies (spikes / collapses) so an
    unlabeled detector can be scored. Inject only where a baseline is established (so a
    spike is genuinely anomalous, not a cold-start zero)."""
    baseline = df["baseline_mean"].to_numpy(dtype=float)
    eligible = np.flatnonzero(baseline > max(1.0, np.median(baseline) / 2))
    k = max(1, int(len(df) * INJECT_FRAC))
    k = min(k, len(eligible))
    chosen = rng.choice(eligible, size=k, replace=False)

    injected = df.copy()
    revenue = injected["revenue"].to_numpy(dtype=float)
    for i, idx in enumerate(chosen):
        revenue[idx] = baseline[idx] * 5.0 + 1.0 if i % 2 == 0 else 0.0  # spike / collapse
    injected["revenue"] = revenue
    labels = np.zeros(len(df), dtype=int)
    labels[chosen] = 1
    return injected, labels


def _evaluate(
    detector: SeasonalAnomalyDetector, df: pd.DataFrame
) -> tuple[float, float, float, float]:
    """Mean ± std detection F1 over N_TRIALS injections, plus mean precision/recall."""
    f1s, precisions, recalls = [], [], []
    for trial in range(N_TRIALS):
        injected, labels = _inject(df, np.random.default_rng(42 + trial))
        preds = detector.flag(injected).astype(int)
        f1s.append(f1_score(labels, preds, zero_division=0))
        precisions.append(precision_score(labels, preds, zero_division=0))
        recalls.append(recall_score(labels, preds, zero_division=0))
    return (
        float(np.mean(f1s)),
        float(np.std(f1s)),
        float(np.mean(precisions)),
        float(np.mean(recalls)),
    )


def train_from_frame(
    df: pd.DataFrame,
    *,
    n_tenants: int,
    artifact: Path = ARTIFACT,
    card_path: Path = CARD,
    results_path: Path | None = None,
) -> ModelCard | None:
    """Fit the seasonal model, compare the 3 detectors on injected anomalies, log each to
    results.csv, and save the winner + card. Returns the card (or None if there is too
    little data). Pure (frame in, artifact out) so a test can redirect the paths."""
    if df.empty or len(df) < MIN_ROWS:
        log.warning("train_anomaly.insufficient_data", rows=len(df))
        return None

    df = df.sort_values("day").reset_index(drop=True)
    results: dict[str, tuple[float, float, float, float]] = {}
    detectors = _candidates(df)
    for name, detector in detectors.items():
        mean, std, precision, recall = _evaluate(detector, df)
        results[name] = (mean, std, precision, recall)
        result = ExperimentResult(
            task=TASK,
            model=name,
            metric=METRIC,
            cv_mean=mean,
            cv_std=std,
            n_splits=N_TRIALS,
            extra=json.dumps({"precision": round(precision, 4), "recall": round(recall, 4)}),
            data_source="synthetic",
        )
        log_result(result) if results_path is None else log_result(result, path=results_path)
        log.info(
            "train_anomaly.candidate",
            model=name,
            f1=round(mean, 3),
            precision=round(precision, 3),
            recall=round(recall, 3),
        )

    winner = max(results, key=lambda k: results[k][0])
    best_mean, best_std, best_precision, best_recall = results[winner]
    detector = detectors[winner]

    artifact.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(detector, artifact)
    threshold = detector.threshold if winner != "isolation_forest" else 0.0
    card = ModelCard(
        task=TASK,
        model=winner,
        metric=METRIC,
        cv_mean=best_mean,
        cv_std=best_std,
        n_splits=N_TRIALS,
        features=detector.regressor_features,
        target="revenue",
        n_rows=len(df),
        n_tenants=n_tenants,
        data_source="synthetic",
        extra={
            "method": winner,
            "threshold": threshold,
            "precision": round(best_precision, 4),
            "recall": round(best_recall, 4),
            "inject_frac": INJECT_FRAC,
        },
        notes="Unsupervised. Expected revenue from a seasonal model (Ramadan/summer/weekend "
        "are expected, not anomalies); flagged on the SCALE-FREE relative residual so one "
        "threshold works across tenants. Scored by injecting synthetic spikes/collapses "
        "(no real labels). Threshold is tunable; this card records the chosen value.",
    )
    card.save(card_path)
    log.info(
        "train_anomaly.saved",
        winner=winner,
        f1=round(best_mean, 3),
        precision=round(best_precision, 3),
        recall=round(best_recall, 3),
        artifact=str(artifact),
    )
    return card


async def train() -> ModelCard | None:
    """Load the dataset from Postgres and train. The one-command entrypoint calls this."""
    engine = create_engine()
    try:
        sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        df = await load_anomaly_dataset(sessionmaker)
        from app.repositories.training_data import tenants_for_training

        async with sessionmaker() as session:
            n_tenants = len(await tenants_for_training(session, min_orders=30))
        return train_from_frame(df, n_tenants=n_tenants)
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging()
    argparse.ArgumentParser(
        description="Train the revenue-anomaly detector (Phase 6)."
    ).parse_args()
    asyncio.run(train())


if __name__ == "__main__":
    main()
