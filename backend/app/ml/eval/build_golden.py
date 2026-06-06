"""Generate the committed golden eval sets (Phase 6, Task 6.11). Run ONCE, commit outputs:

    cd backend && uv run python -m app.ml.eval.build_golden

The golden sets are deterministic (fixed seed 99, DIFFERENT from training's 42, so they are
held-out draws of the same synthetic distribution) and committed as JSON under
app/ml/eval/golden/. CI loads THESE files (never regenerates), so a model that regresses is
caught against a frozen, inspectable benchmark — and the eval is robust to later changes in
the generators. Synthetic, like everything in Phase 6 (the honesty caveat applies).

  - demand: 20 (feature-row, actual units) cases for a bakery product.
  - churn:  ~24 labeled customers (RFM features + churned), built leakage-free at one cutoff.
  - anomaly: a ~150-day revenue feature frame with ~10 KNOWN injected anomalies labeled.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

from app.infra.logging import get_logger
from app.ml.features import anomaly as anomaly_feats
from app.ml.features import churn as churn_feats
from app.ml.features import demand as demand_feats
from app.ml.seed_history import BAKERY, daily_units

log = get_logger("ml.eval.build_golden")

GOLDEN_SEED = 99
GOLDEN_DIR = Path(__file__).parent / "golden"
START = date(2024, 6, 1)


def _demand_cases() -> list[dict]:
    rng = random.Random(GOLDEN_SEED)
    spec = BAKERY.products[0]
    rows = [
        (
            spec.name_en,
            START + timedelta(days=i),
            daily_units(rng, BAKERY, spec, START + timedelta(days=i)),
        )
        for i in range(365)
    ]
    frame = demand_feats.build_demand_features(rows).tail(20)
    cols = [*demand_feats.feature_columns(), "units"]
    return frame[cols].to_dict(orient="records")


def _churn_cases() -> list[dict]:
    rng = random.Random(GOLDEN_SEED)
    as_of = date(2025, 5, 1)
    orders: list[tuple] = []
    for i in range(24):
        churns = i % 2 == 0  # half churned, half active — a balanced, inspectable golden set
        for _ in range(rng.randint(3, 8)):
            # Non-overlapping recency bands so the golden labels are unambiguous: churners
            # last seen long ago (>=130d), active customers recently (<=60d).
            age = rng.randint(130, 300) if churns else rng.randint(1, 60)
            orders.append((f"g{i}", as_of - timedelta(days=age), rng.randint(50_000, 500_000)))
        if not churns:  # a return within the horizon → churned=0
            orders.append(
                (f"g{i}", as_of + timedelta(days=rng.randint(1, 29)), rng.randint(50_000, 500_000))
            )
    frame = churn_feats.build_churn_features(orders, as_of=as_of)
    cols = [*churn_feats.feature_columns(), "churned"]
    return frame[cols].to_dict(orient="records")


def _anomaly_cases() -> list[dict]:
    rng = random.Random(GOLDEN_SEED)
    rows = []
    for i in range(150):
        day = START + timedelta(days=i)
        revenue = sum(
            daily_units(rng, BAKERY, spec, day) * spec.price_lbp for spec in BAKERY.products
        )
        rows.append((day, revenue))
    frame = anomaly_feats.build_anomaly_features(rows).reset_index(drop=True)
    frame["revenue"] = frame["revenue"].astype(float)  # so injected spikes/collapses fit

    # Inject ~10 KNOWN anomalies on established-baseline days, spaced out; overwrite only the
    # day's revenue (keep its clean trailing baseline) so the label is unambiguous — exactly
    # how the trainer injected (spike = 5x baseline, collapse = 0).
    labels = [0] * len(frame)
    eligible = [i for i in range(40, len(frame)) if frame.loc[i, "baseline_mean"] > 0]
    chosen = eligible[::11][:10]
    for k, i in enumerate(chosen):
        base = float(frame.loc[i, "baseline_mean"])
        frame.loc[i, "revenue"] = base * 5.0 + 1.0 if k % 2 == 0 else 0.0
        labels[i] = 1
    frame["is_anomaly"] = labels
    frame["day"] = frame["day"].astype(str)
    cols = ["day", "revenue", *anomaly_feats.feature_columns(), "is_anomaly"]
    return frame[cols].to_dict(orient="records")


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, cases in (
        ("demand", _demand_cases()),
        ("churn", _churn_cases()),
        ("anomaly", _anomaly_cases()),
    ):
        path = GOLDEN_DIR / f"{name}.json"
        # Trailing newline for the end-of-file-fixer pre-commit hook.
        path.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log.info("ml.eval.golden.written", model=name, cases=len(cases), path=str(path))


if __name__ == "__main__":
    main()
