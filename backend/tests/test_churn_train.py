"""Task 6.8 — the churn classifier training pipeline.

Proves the trainer satisfies Constitution IV (and the imbalanced-problem rules) without
needing the DB:
  - it compares >=3 candidates and logs EACH to results.csv with the headline metric
    (F1 on the churned class) mean +- std AND the full per-class metrics in `extra`;
  - preprocessing is INSIDE the sklearn.Pipeline (the saved artifact is a Pipeline);
  - it selects the HIGHEST churned-class-F1 winner and saves a loadable artifact + a card
    whose feature list matches the builder and whose `extra` carries per-class metrics;
  - a single-class or empty frame trains nothing (returns None) rather than crashing.

`train_from_frame` is pure (frame in, artifact out), so we feed it a synthetic imbalanced
frame built through the REAL churn feature builder and redirect the artifact/results paths
to tmp_path — the real results.csv and the committed churn.joblib are never touched.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

import joblib
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from app.ml.churn import train as churn_train
from app.ml.features.churn import build_churn_features, feature_columns


def _synthetic_frame(*, churn_share: float = 0.3, n_customers: int = 150) -> pd.DataFrame:
    """An imbalanced churn frame: ~`churn_share` of customers place no order in the 30-day
    forward window (churned=1), the rest do (churned=0). Built through the real builder so
    recency/frequency/monetary are exactly what training sees."""
    rng = random.Random(42)
    as_of = date(2025, 5, 1)
    orders: list[tuple] = []
    for i in range(n_customers):
        cid = f"c{i}"
        for _ in range(rng.randint(2, 8)):  # past orders (features)
            past_day = as_of - timedelta(days=rng.randint(31, 300))
            orders.append((cid, past_day, rng.randint(500, 5000)))
        if rng.random() >= churn_share:  # returns within the horizon → churned=0
            orders.append((cid, as_of + timedelta(days=rng.randint(1, 29)), rng.randint(500, 5000)))
    return build_churn_features(orders, as_of=as_of)


@pytest.fixture
def out_paths(tmp_path) -> tuple[Path, Path, Path]:
    return tmp_path / "churn.joblib", tmp_path / "churn_card.json", tmp_path / "results.csv"


def _train(frame: pd.DataFrame, out_paths, *, n_tenants: int = 1):
    artifact, card, results = out_paths
    return churn_train.train_from_frame(
        frame, n_tenants=n_tenants, artifact=artifact, card_path=card, results_path=results
    )


def test_logs_three_candidates_with_per_class_metrics(out_paths) -> None:
    card = _train(_synthetic_frame(), out_paths)
    assert card is not None
    rows = pd.read_csv(out_paths[2])
    assert set(rows["model"]) == {"logreg", "random_forest", "xgboost"}
    assert (rows["task"] == "churn").all()
    assert (rows["metric"] == "f1_pos").all()
    # Every row carries per-class precision/recall/F1 for BOTH classes (not macro-only).
    for raw in rows["extra"]:
        per_class = json.loads(raw)
        assert set(per_class) == {"0", "1"}
        assert set(per_class["1"]) == {"precision", "recall", "f1"}


def test_winner_has_highest_f1_pos(out_paths) -> None:
    card = _train(_synthetic_frame(), out_paths)
    rows = pd.read_csv(out_paths[2])
    best = rows.loc[rows["cv_mean"].idxmax(), "model"]  # higher F1 is better
    assert card.model == best


def test_artifact_is_a_pipeline_and_predicts_labels(out_paths) -> None:
    _train(_synthetic_frame(), out_paths)
    model = joblib.load(out_paths[0])
    assert isinstance(model, Pipeline)
    frame = _synthetic_frame()
    preds = set(model.predict(frame[feature_columns()]).tolist())
    assert preds <= {0, 1}  # a binary classifier


def test_card_has_label_rule_and_per_class_metrics(out_paths) -> None:
    card = _train(_synthetic_frame(), out_paths)
    assert card.task == "churn"
    assert card.target == "churned"
    assert card.metric == "f1_pos"
    assert card.data_source == "synthetic"
    assert card.features == feature_columns()
    assert "per_class" in card.extra and {"0", "1"} <= set(card.extra["per_class"])
    assert card.extra["n_pos"] > 0 and card.extra["n_neg"] > 0


def test_recall_on_churned_class_is_reported_and_real(out_paths) -> None:
    # The whole point of the imbalanced task: we can SEE recall on the positive class, and
    # with class weighting it catches a meaningful share of churners (not ~0).
    card = _train(_synthetic_frame(), out_paths)
    assert card.extra["per_class"]["1"]["recall"] > 0.5


def test_single_class_trains_nothing(out_paths) -> None:
    # Everyone returns (no churn) → one class only → nothing to train.
    frame = _synthetic_frame(churn_share=0.0)
    assert (frame["churned"] == 0).all()
    assert _train(frame, out_paths) is None
    assert not out_paths[0].exists()


def test_empty_frame_trains_nothing(out_paths) -> None:
    assert _train(pd.DataFrame({"churned": []}), out_paths, n_tenants=0) is None
    assert not out_paths[0].exists()
