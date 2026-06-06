"""Task 6.5 — the demand forecaster training pipeline.

Proves the trainer satisfies Constitution IV without needing the DB:
  - it compares >=3 candidates and logs EACH to results.csv with CV mean +- std;
  - preprocessing is INSIDE the sklearn.Pipeline (the saved artifact is a Pipeline, so a
    fold's imputer/scaler is fit on train only — no leakage);
  - it selects the lowest-MAE winner and saves a loadable artifact + a model card whose
    feature list matches the builder;
  - the saved pipeline can predict on a fresh feature frame.

`train_from_frame` is pure (frame in, artifact out), so we feed it a synthetic seasonal
frame and redirect the artifact/results paths to tmp_path — the real results.csv and the
committed demand.joblib are never touched by the test.
"""

import random
from datetime import date
from pathlib import Path

import joblib
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from app.ml.demand import train as demand_train
from app.ml.features.demand import build_demand_features, feature_columns
from app.ml.model_card import ModelCard
from app.ml.seed_history import BAKERY, daily_units


def _synthetic_frame() -> pd.DataFrame:
    """A full year of one product's seasonal demand, run through the real feature
    builder — enough rows and signal for a 5-fold TimeSeriesSplit to be meaningful."""
    rng = random.Random(42)
    spec = BAKERY.products[0]
    start = date(2024, 6, 1)
    rows = []
    for i in range(360):
        d = date.fromordinal(start.toordinal() + i)
        rows.append((str(spec.name_en), d, daily_units(rng, BAKERY, spec, d)))
    return build_demand_features(rows)


@pytest.fixture
def out_paths(tmp_path) -> tuple[Path, Path, Path]:
    """Tmp artifact / card / results paths so the test never writes the committed model
    or the real results.csv (the trainer takes these as parameters)."""
    return tmp_path / "demand.joblib", tmp_path / "demand_card.json", tmp_path / "results.csv"


def _train(frame: pd.DataFrame, out_paths, *, n_tenants: int = 1) -> ModelCard | None:
    artifact, card, results = out_paths
    return demand_train.train_from_frame(
        frame, n_tenants=n_tenants, artifact=artifact, card_path=card, results_path=results
    )


def test_train_logs_three_candidates(out_paths) -> None:
    card = _train(_synthetic_frame(), out_paths)
    assert card is not None
    rows = pd.read_csv(out_paths[2])
    # Every candidate is recorded (the constitution's ">=3 models" rule), all for demand.
    assert set(rows["model"]) == {"ridge", "hgbr", "xgboost"}
    assert (rows["task"] == "demand").all()
    assert (rows["n_splits"] == demand_train.N_SPLITS).all()
    assert (rows["cv_std"] >= 0).all()


def test_winner_has_lowest_cv_mae(out_paths) -> None:
    card = _train(_synthetic_frame(), out_paths)
    rows = pd.read_csv(out_paths[2])
    best = rows.loc[rows["cv_mean"].idxmin(), "model"]
    assert card.model == best


def test_artifact_is_a_pipeline_and_predicts(out_paths) -> None:
    _train(_synthetic_frame(), out_paths)
    artifact = out_paths[0]
    assert artifact.exists()
    model = joblib.load(artifact)
    # It's a sklearn Pipeline (preprocessing inside → no leakage) and can predict.
    assert isinstance(model, Pipeline)
    frame = _synthetic_frame()
    preds = model.predict(frame[feature_columns()])
    assert len(preds) == len(frame)


def test_card_features_match_builder(out_paths) -> None:
    _train(_synthetic_frame(), out_paths)
    card = ModelCard.load(out_paths[1])
    assert card.features == feature_columns()
    assert card.task == "demand"
    assert card.target == "units"
    assert card.data_source == "synthetic"


def test_empty_frame_trains_nothing(out_paths) -> None:
    card = _train(pd.DataFrame(), out_paths, n_tenants=0)
    assert card is None
    assert not out_paths[0].exists()
