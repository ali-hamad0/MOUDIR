"""Task 6.4 — feature builders + the LEAKAGE boundary.

The headline test is leakage (AD-6.6, the constitution's "most common bug"): a feature
must never see data on/after the prediction cutoff. We prove it three ways —
  - the `as_of` cutoff strictly shrinks the rows a builder sees (and never lets a
    future-dated row through);
  - demand lag/rolling features equal hand-computed PAST-only values (today's target is
    never in its own window);
  - the churn LABEL uses the forward window, but no FEATURE does.
Plus seasonality correctness and an end-to-end build on the seeded repo reads.

Builders are pure functions, so most tests need no DB; the integration test seeds via
the 6.2 generator on the rolled-back db_session and reads through the 6.3 repo.
"""

import random
from datetime import date, timedelta

from app.ml.features import anomaly, churn, demand
from app.ml.features.anomaly import RevenueRow
from app.ml.features.churn import CustomerOrder
from app.ml.features.demand import DemandRow
from app.ml.seed_history import BAKERY, _seed_tenant
from app.repositories.training_data import TrainingDataRepository

# ── helpers ──────────────────────────────────────────────────────────────────


def _demand_series(product: str, start: date, n: int) -> list[DemandRow]:
    # Deterministic increasing series so lags are easy to verify by hand.
    return [DemandRow(product, start + timedelta(days=i), units=i + 1) for i in range(n)]


# ── leakage: the as_of boundary ──────────────────────────────────────────────


def test_demand_as_of_excludes_future_rows() -> None:
    rows = _demand_series("p1", date(2025, 1, 1), 40)
    cutoff = date(2025, 1, 20)
    feats = demand.build_demand_features(rows, as_of=cutoff)
    assert len(feats) > 0
    # Not a single feature row is dated on/after the cutoff.
    assert (feats["day"] < cutoff).all()


def test_demand_earlier_as_of_sees_strictly_less() -> None:
    rows = _demand_series("p1", date(2025, 1, 1), 60)
    early = demand.build_demand_features(rows, as_of=date(2025, 1, 15))
    late = demand.build_demand_features(rows, as_of=date(2025, 2, 1))
    assert 0 < len(early) < len(late)  # a tighter cutoff yields fewer rows


def test_demand_lags_are_past_only() -> None:
    rows = _demand_series("p1", date(2025, 1, 1), 30)  # units == day index + 1
    feats = demand.build_demand_features(rows).set_index("day")
    # On day 10 (units=10), lag_1 must be day 9's units (=9), lag_7 day 3's (=3).
    target = date(2025, 1, 10)
    assert feats.loc[target, "units"] == 10
    assert feats.loc[target, "lag_1"] == 9
    assert feats.loc[target, "lag_7"] == 3
    # The rolling mean ends YESTERDAY — it must be strictly below today's value on a
    # monotonically increasing series (proof today is not in its own window).
    assert feats.loc[target, "roll_mean_7"] < feats.loc[target, "units"]


def test_churn_features_are_past_only_label_is_future() -> None:
    cust = "c1"
    orders = [
        CustomerOrder(cust, date(2025, 1, 1), 100),
        CustomerOrder(cust, date(2025, 1, 10), 200),
        # A future order AFTER the cutoff — must affect ONLY the label, not features.
        CustomerOrder(cust, date(2025, 2, 5), 999),
    ]
    as_of = date(2025, 1, 20)
    feats = churn.build_churn_features(orders, as_of=as_of, horizon_days=30).set_index(
        "customer_id"
    )
    row = feats.loc[cust]
    # Features come only from the two PAST orders (frequency 2, monetary 300) — the 999
    # future order is excluded from every feature.
    assert row["frequency"] == 2
    assert row["monetary_lbp"] == 300
    assert row["recency_days"] == (as_of - date(2025, 1, 10)).days
    # The customer DID return within the horizon (Feb 5 ≤ Jan 20+30) → not churned.
    assert row["churned"] == 0


def test_churn_label_marks_no_return_as_churned() -> None:
    cust = "c1"
    orders = [CustomerOrder(cust, date(2025, 1, 1), 100)]  # no future order
    feats = churn.build_churn_features(orders, as_of=date(2025, 1, 20), horizon_days=30)
    assert feats.set_index("customer_id").loc[cust, "churned"] == 1


def test_anomaly_baseline_is_past_only() -> None:
    # Flat 100/day then a spike — the spike day's baseline must reflect the flat past,
    # so its deviation is large and positive (today not in its own baseline).
    rows = [RevenueRow(date(2025, 1, 1) + timedelta(days=i), 100) for i in range(20)]
    rows.append(RevenueRow(date(2025, 1, 21), 1000))
    feats = anomaly.build_anomaly_features(rows).set_index("day")
    spike = feats.loc[date(2025, 1, 21)]
    assert spike["revenue"] == 1000
    assert abs(spike["baseline_mean"] - 100) < 1e-6  # baseline = the flat past, not 1000
    assert spike["deviation"] > 800


def test_anomaly_as_of_bounds_history() -> None:
    rows = [RevenueRow(date(2025, 1, 1) + timedelta(days=i), 100) for i in range(30)]
    feats = anomaly.build_anomaly_features(rows, as_of=date(2025, 1, 15))
    assert (feats["day"] < date(2025, 1, 15)).all()


# ── seasonality correctness ──────────────────────────────────────────────────


def test_seasonality_flags_on_features() -> None:
    rows = _demand_series("p1", date(2025, 2, 25), 20)  # spans into Ramadan 2025 (Mar 1)
    feats = demand.build_demand_features(rows).set_index("day")
    assert feats.loc[date(2025, 3, 5), "is_ramadan"] == 1
    assert feats.loc[date(2025, 2, 26), "is_ramadan"] == 0


# ── integration: build on the seeded repo reads ──────────────────────────────


async def test_build_from_repo_reads(db_session) -> None:
    stats = await _seed_tenant(
        db_session,
        BAKERY,
        start=date(2025, 2, 20),
        end=date(2025, 3, 20),  # spans Ramadan
        n_customers=8,
        churn_fraction=0.3,
        rng=random.Random(42),
    )
    repo = TrainingDataRepository(db_session)

    demand_rows = await repo.daily_product_demand(stats.tenant_id)
    demand_feats = demand.build_demand_features(demand_rows)
    assert len(demand_feats) > 0
    assert set(demand.feature_columns()).issubset(demand_feats.columns)
    # Ramadan days are present and flagged in the built features.
    assert demand_feats["is_ramadan"].sum() > 0

    revenue_rows = await repo.daily_revenue(stats.tenant_id)
    anomaly_feats = anomaly.build_anomaly_features(revenue_rows)
    assert len(anomaly_feats) > 0
    assert set(anomaly.feature_columns()).issubset(anomaly_feats.columns)

    # Churn: build from the per-order read at a mid-window cutoff. Both classes should
    # appear given the 30% churn_fraction (some customers labeled churned, some not).
    order_rows = await repo.customer_orders(stats.tenant_id)
    churn_feats = churn.build_churn_features(order_rows, as_of=date(2025, 3, 10), horizon_days=7)
    assert len(churn_feats) > 0
    assert set(churn.feature_columns()).issubset(churn_feats.columns)
    assert churn_feats["churned"].nunique() == 2  # both labels present (imbalanced ok)


def test_empty_inputs_return_empty_frames() -> None:
    assert demand.build_demand_features([]).empty
    assert anomaly.build_anomaly_features([]).empty
    assert churn.build_churn_features([], as_of=date(2025, 1, 1)).empty
    # Empty frames still carry the right columns (so downstream concat/selection works).
    assert set(demand.feature_columns()).issubset(demand.build_demand_features([]).columns)
