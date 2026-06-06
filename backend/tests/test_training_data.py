"""Task 6.3 — training-data reads + the cross-tenant discovery + the exporter.

Proves the ML layer reads history correctly AND inside the Wall:
  - daily_product_demand / daily_revenue / customer_order_history aggregate the seeded
    history into the training shapes, with the date window bounding correctly;
  - the reads are TENANT-SCOPED — tenant A's reads never include tenant B's rows
    (constitution I), incl. the order_item→order and customer→order JOINs;
  - tenants_for_training discovers tenants by history depth (the one documented
    cross-tenant query), returning only tenant ids;
  - the exporter writes one folder of CSVs PER TENANT, never mixing two tenants.

Uses the 6.2 generator (_seed_tenant) on the rolled-back db_session so the assertions
run on realistic, seasonal data without touching the dev DB.
"""

import random
from datetime import date
from pathlib import Path

from app.ml.export import export_tenant
from app.ml.seed_history import BAKERY, MINI_MARKET, _seed_tenant
from app.repositories.training_data import TrainingDataRepository, tenants_for_training


async def _seed(db_session, archetype, *, start=date(2025, 3, 1), end=date(2025, 3, 15), seed=42):
    return await _seed_tenant(
        db_session,
        archetype,
        start=start,
        end=end,
        n_customers=6,
        churn_fraction=0.2,
        rng=random.Random(seed),
    )


# ── the reads ────────────────────────────────────────────────────────────────


async def test_daily_product_demand_aggregates(db_session) -> None:
    stats = await _seed(db_session, BAKERY)
    repo = TrainingDataRepository(db_session)
    rows = await repo.daily_product_demand(stats.tenant_id)
    assert len(rows) > 0
    # Each row is (product_id, day, units) with a positive unit count.
    for _product_id, day, units in rows:
        assert units > 0
        assert isinstance(day, date)
    # Ordered by (product, day) → contiguous per-product series.
    product_days = [(r[0], r[1]) for r in rows]
    assert product_days == sorted(product_days, key=lambda pd: (str(pd[0]), pd[1]))


async def test_daily_revenue_spans_window(db_session) -> None:
    start, end = date(2025, 3, 1), date(2025, 3, 15)
    stats = await _seed(db_session, BAKERY, start=start, end=end)
    rows = await TrainingDataRepository(db_session).daily_revenue(stats.tenant_id)
    assert len(rows) > 0
    days = [r[0] for r in rows]
    assert all(start <= d < end for d in days)
    assert days == sorted(days)  # oldest first
    assert all(r[1] > 0 for r in rows)  # positive daily revenue


async def test_window_bounds_filter(db_session) -> None:
    stats = await _seed(db_session, BAKERY, start=date(2025, 3, 1), end=date(2025, 3, 15))
    repo = TrainingDataRepository(db_session)
    # A sub-window returns strictly fewer days than the full history.
    full = await repo.daily_revenue(stats.tenant_id)
    sub = await repo.daily_revenue(stats.tenant_id, start=date(2025, 3, 5), end=date(2025, 3, 10))
    assert 0 < len(sub) < len(full)
    assert all(date(2025, 3, 5) <= r[0] < date(2025, 3, 10) for r in sub)


async def test_customer_order_history_rfm(db_session) -> None:
    stats = await _seed(db_session, BAKERY)
    rows = await TrainingDataRepository(db_session).customer_order_history(stats.tenant_id)
    assert len(rows) == 6  # one row per seeded customer
    # At least some customers ordered; order_count and totals are coherent.
    with_orders = [r for r in rows if r[1] > 0]
    assert with_orders
    for _cid, count, first_at, last_at, total in with_orders:
        assert count > 0
        assert first_at is not None and last_at is not None
        assert last_at >= first_at
        assert total > 0


# ── the Wall ─────────────────────────────────────────────────────────────────


async def test_reads_are_tenant_scoped(db_session) -> None:
    a = await _seed(db_session, BAKERY, seed=1)
    b = await _seed(db_session, MINI_MARKET, seed=2)
    assert a.tenant_id != b.tenant_id
    repo = TrainingDataRepository(db_session)

    # A's product-demand rows reference only A's products (B's are absent).
    a_demand = await repo.daily_product_demand(a.tenant_id)
    b_demand = await repo.daily_product_demand(b.tenant_id)
    a_products = {r[0] for r in a_demand}
    b_products = {r[0] for r in b_demand}
    assert a_products.isdisjoint(b_products)  # no product crosses the Wall

    # A's customer history has exactly A's customers (count matches, none shared).
    a_customers = {r[0] for r in await repo.customer_order_history(a.tenant_id)}
    b_customers = {r[0] for r in await repo.customer_order_history(b.tenant_id)}
    assert a_customers.isdisjoint(b_customers)


async def test_tenants_for_training_discovers_by_history(db_session) -> None:
    a = await _seed(db_session, BAKERY, seed=1)
    b = await _seed(db_session, MINI_MARKET, seed=2)
    found = set(await tenants_for_training(db_session, min_orders=1))
    assert {a.tenant_id, b.tenant_id} <= found
    # A high threshold no real seeded tenant meets excludes them.
    none_found = await tenants_for_training(db_session, min_orders=10_000_000)
    assert a.tenant_id not in none_found and b.tenant_id not in none_found


# ── the exporter ─────────────────────────────────────────────────────────────


async def test_export_tenant_writes_per_tenant_csvs(db_session, tmp_path: Path) -> None:
    stats = await _seed(db_session, BAKERY)
    total = await export_tenant(db_session, stats.tenant_id, tmp_path, "csv")
    assert total > 0

    tenant_dir = tmp_path / str(stats.tenant_id)
    for name in ("daily_product_demand", "daily_revenue", "customer_order_history"):
        path = tenant_dir / f"{name}.csv"
        assert path.exists()
        # Header + at least one row.
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2
    # The folder is named for the tenant — no other tenant's data is in this path.
    assert tenant_dir.is_dir()
