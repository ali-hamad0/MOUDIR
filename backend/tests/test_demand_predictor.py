"""Task 6.6 — the DemandPredictor serving seam.

Proves the seam loads the REAL trained pipeline and predicts (the present-artifact branch
that test_ml_scaffold.py deliberately left to this task), AND that it degrades to the
offline stub exactly like ocr_mode/embedding_mode:

  - ml_mode="trained" + the committed demand.joblib/_card.json present → a real
    TrainedDemandPredictor whose predict_quantity returns a non-negative int from features
    built at predict-time by the SAME app/ml/features/demand.py builder the trainer used;
  - cold start (no history / unknown product) → None, so the caller falls back to its
    documented default (AD-6.5) — the model is never asked to hallucinate;
  - ml_mode="stub" (the CI/dev default) → the stub, returning None;
  - ml_mode="trained" but the artifact missing → the stub (logged), never a crash.

Records are generated through the real seasonal generator (app/ml/seed_history) so the
features carry learnable signal — mirroring test_demand_train.py, but no DB and no
training: this loads the committed artifact and only predicts.
"""

import random
from datetime import date, timedelta
from uuid import uuid4

from app.infra.settings import Settings
from app.ml.demand.predictor import TrainedDemandPredictor
from app.ml.predictors import StubDemandPredictor, build_demand_predictor
from app.ml.seed_history import BAKERY, daily_units


def _history(product_id, *, days: int = 180, start: date = date(2024, 6, 1)) -> list[tuple]:
    """`days` of one product's seasonal daily demand in the daily_product_demand shape
    ((product_id, day, units) rows), run through the real generator so the lags/rollings
    and Ramadan/summer signal the model trained on are actually present."""
    rng = random.Random(7)
    spec = BAKERY.products[0]
    return [
        (
            product_id,
            start + timedelta(days=i),
            daily_units(rng, BAKERY, spec, start + timedelta(days=i)),
        )
        for i in range(days)
    ]


def _trained() -> TrainedDemandPredictor:
    settings = Settings.model_construct(ml_mode="trained", ml_demand_artifact="demand.joblib")
    predictor = build_demand_predictor(settings)
    assert isinstance(predictor, TrainedDemandPredictor)  # the real-load branch fired
    return predictor


# ── real-load path ───────────────────────────────────────────────────────────


def test_trained_mode_loads_real_predictor() -> None:
    # The committed artifact + card are present → the factory returns the real predictor,
    # not the stub. (This is the present-artifact branch test_ml_scaffold left to 6.6.)
    assert isinstance(_trained(), TrainedDemandPredictor)


def test_real_predictor_returns_a_nonnegative_int() -> None:
    predictor = _trained()
    tenant_id, product_id = uuid4(), uuid4()
    qty = predictor.predict_quantity(tenant_id, product_id, _history(product_id))
    assert isinstance(qty, int)
    assert qty >= 0


def test_real_predictor_honours_as_of() -> None:
    # Forecasting an explicit future day works and still yields a non-negative int.
    predictor = _trained()
    product_id = uuid4()
    history = _history(product_id, days=120)
    as_of = history[-1][1] + timedelta(days=1)
    qty = predictor.predict_quantity(uuid4(), product_id, history, as_of=as_of)
    assert isinstance(qty, int)
    assert qty >= 0


def test_real_predictor_reads_feature_list_from_card() -> None:
    # The feature columns come from the card, not a hard-coded list — so the predictor and
    # the trainer can never silently disagree.
    from app.ml.features.demand import feature_columns

    assert _trained()._features == feature_columns()


# ── cold start → None (caller falls back) ────────────────────────────────────


def test_real_predictor_no_history_returns_none() -> None:
    predictor = _trained()
    assert predictor.predict_quantity(uuid4(), uuid4(), []) is None


def test_real_predictor_unknown_product_returns_none() -> None:
    # History exists, but not for the asked product → no signal → None.
    predictor = _trained()
    known = uuid4()
    assert predictor.predict_quantity(uuid4(), uuid4(), _history(known)) is None


# ── stub fallback (the CI/dev default) ───────────────────────────────────────


def test_stub_mode_returns_stub() -> None:
    predictor = build_demand_predictor(Settings.model_construct(ml_mode="stub"))
    assert isinstance(predictor, StubDemandPredictor)
    assert predictor.predict_quantity(uuid4(), uuid4(), _history(uuid4())) is None


def test_trained_mode_missing_artifact_degrades_to_stub() -> None:
    settings = Settings.model_construct(
        ml_mode="trained", ml_demand_artifact="does-not-exist-demand.joblib"
    )
    assert isinstance(build_demand_predictor(settings), StubDemandPredictor)
