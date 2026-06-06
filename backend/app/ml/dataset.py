"""Training-set assembly (Phase 6). Turn the tenant-scoped DB reads into the stacked
feature frames the trainers consume — shared by demand (6.5), churn (6.8), anomaly (6.9).

The Wall during training (constitution I, AD-6.6): we discover trainable tenants via the
ONE documented cross-tenant query (`tenants_for_training`), then read EACH tenant inside
the Wall (TrainingDataRepository) and build its features, finally stacking the per-tenant
frames. The artifact that results is shared weights — fine — but every feature ROW that
went into it is one tenant's own data; nothing is read across the Wall, and neither
tenant_id nor product_id is ever a feature (only demand patterns are), so the model can't
learn one tenant FROM another.

These are async loaders (they touch the DB); a trainer calls them once at the top, then
the modelling is pure pandas/sklearn. Colab skips these and loads the exported CSVs
instead ([[phase6-ml-colab-decision]]).
"""

from collections.abc import Sequence
from datetime import date

import pandas as pd
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra.logging import get_logger
from app.ml.features import anomaly, churn, demand
from app.repositories.training_data import TrainingDataRepository, tenants_for_training

log = get_logger(__name__)

# Only train on tenants with at least this much history (avoids fitting on a near-empty
# brand-new shop). A modest floor; the synthetic tenants have thousands of orders.
MIN_ORDERS_TO_TRAIN = 30


async def load_demand_dataset(
    sessionmaker: async_sessionmaker, *, min_orders: int = MIN_ORDERS_TO_TRAIN
) -> tuple[pd.DataFrame, int]:
    """Stacked demand features across all trainable tenants (full history, no as_of).

    One row per (tenant's product, day) with the leakage-free demand features (target
    column `units`). Returns (frame, n_tenants) so the trainer can record how many
    tenants the artifact was trained across without putting tenant_id in the features.
    """
    frames: list[pd.DataFrame] = []
    async with sessionmaker() as session:
        tenant_ids = await tenants_for_training(session, min_orders=min_orders)
    for tenant_id in tenant_ids:
        async with sessionmaker() as session:
            rows = await TrainingDataRepository(session).daily_product_demand(tenant_id)
        feats = demand.build_demand_features(rows)
        if not feats.empty:
            frames.append(feats)
    out = _concat(frames)
    log.info("ml.dataset.demand", tenants=len(tenant_ids), rows=len(out))
    return out, len(tenant_ids)


async def load_anomaly_dataset(
    sessionmaker: async_sessionmaker, *, min_orders: int = MIN_ORDERS_TO_TRAIN
) -> pd.DataFrame:
    """Stacked daily-revenue anomaly features across all trainable tenants."""
    frames: list[pd.DataFrame] = []
    async with sessionmaker() as session:
        tenant_ids = await tenants_for_training(session, min_orders=min_orders)
    for tenant_id in tenant_ids:
        async with sessionmaker() as session:
            rows = await TrainingDataRepository(session).daily_revenue(tenant_id)
        feats = anomaly.build_anomaly_features(rows)
        if not feats.empty:
            frames.append(feats)
    out = _concat(frames)
    log.info("ml.dataset.anomaly", tenants=len(tenant_ids), rows=len(out))
    return out


async def load_churn_dataset(
    sessionmaker: async_sessionmaker,
    *,
    as_ofs: Sequence[date],
    horizon_days: int = churn.DEFAULT_HORIZON_DAYS,
    min_orders: int = MIN_ORDERS_TO_TRAIN,
) -> pd.DataFrame:
    """Stacked churn features+label across all trainable tenants at SEVERAL cutoffs.

    `as_ofs` is required (the `churned` label is defined relative to each cutoff). One row
    per (customer, cutoff) for customers with a PAST order at that cutoff — so the same
    customer contributes several rows over time (still active early → churned later),
    giving the classifier temporal variety and more rows than a single snapshot. Each
    tenant's orders are fetched ONCE (tenant-scoped — the Wall) and the leakage-free
    features are rebuilt per cutoff (features strictly before the cutoff, label in its
    forward window — AD-6.6).
    """
    frames: list[pd.DataFrame] = []
    async with sessionmaker() as session:
        tenant_ids = await tenants_for_training(session, min_orders=min_orders)
    for tenant_id in tenant_ids:
        async with sessionmaker() as session:
            rows = await TrainingDataRepository(session).customer_orders(tenant_id)
        for as_of in as_ofs:
            feats = churn.build_churn_features(rows, as_of=as_of, horizon_days=horizon_days)
            if not feats.empty:
                frames.append(feats)
    out = _concat(frames)
    log.info("ml.dataset.churn", tenants=len(tenant_ids), rows=len(out), cutoffs=len(as_ofs))
    return out


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
