"""Export training data to flat files for offline training (Phase 6, Task 6.3 / AD-6.1).

Training is code-first and runs against Postgres on the host; but Colab has no Postgres,
so a notebook trains from an EXPORTED SNAPSHOT instead (the optional-Colab escape hatch,
[[phase6-ml-colab-decision]]). This module reads the same tenant-scoped training views
(TrainingDataRepository) and writes one set of files per tenant under a chosen directory.

Output (CSV by default — zero extra deps; parquet is opt-in and needs `pyarrow`):
    <out>/<tenant_id>/daily_product_demand.csv
    <out>/<tenant_id>/daily_revenue.csv
    <out>/<tenant_id>/customer_order_history.csv

The export is itself tenant-scoped: it discovers tenants via the one documented
cross-tenant query (`tenants_for_training`), then reads each tenant's rows inside the
Wall and writes them to that tenant's own folder — no file ever mixes two tenants.

Run on the HOST (the DB is on localhost; Docker DNS is blocked in-container):
    cd backend
    DATABASE_URL=postgresql+asyncpg://modir:modir@127.0.0.1:5432/modir \
    REDIS_URL=redis://127.0.0.1:6379/0 VAULT_ADDR=http://127.0.0.1:8200 \
    VAULT_TOKEN=root uv run python -m app.ml.export --out ./ml_export
"""

import argparse
import asyncio
from pathlib import Path

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import create_engine
from app.infra.logging import get_logger
from app.repositories.training_data import TrainingDataRepository, tenants_for_training

log = get_logger("ml_export")

# The default snapshot root, relative to the backend cwd. Git-ignored (it is data, not
# code); the user uploads it to Colab manually.
DEFAULT_OUT = Path("ml_export")


def _write(df: pd.DataFrame, path: Path, fmt: str) -> None:
    """Write a frame as CSV (default) or parquet (opt-in; needs pyarrow)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "parquet":
        try:
            df.to_parquet(path, index=False)
        except ImportError as exc:  # pyarrow not installed
            raise SystemExit(
                "parquet export needs 'pyarrow' (uv add pyarrow), or use --format csv"
            ) from exc
    else:
        df.to_csv(path, index=False)


async def export_tenant(session: AsyncSession, tenant_id, out_root: Path, fmt: str) -> int:
    """Export one tenant's three training views into its own folder. Returns the total
    row count written (handy for the summary / tests)."""
    repo = TrainingDataRepository(session)
    tenant_dir = out_root / str(tenant_id)
    ext = "parquet" if fmt == "parquet" else "csv"

    demand = await repo.daily_product_demand(tenant_id)
    revenue = await repo.daily_revenue(tenant_id)
    customers = await repo.customer_order_history(tenant_id)

    demand_df = pd.DataFrame(demand, columns=["product_id", "day", "units"])
    revenue_df = pd.DataFrame(revenue, columns=["day", "total_lbp"])
    customers_df = pd.DataFrame(
        customers,
        columns=[
            "customer_id",
            "order_count",
            "first_order_at",
            "last_order_at",
            "total_spent_lbp",
        ],
    )

    _write(demand_df, tenant_dir / f"daily_product_demand.{ext}", fmt)
    _write(revenue_df, tenant_dir / f"daily_revenue.{ext}", fmt)
    _write(customers_df, tenant_dir / f"customer_order_history.{ext}", fmt)

    total = len(demand_df) + len(revenue_df) + len(customers_df)
    log.info(
        "ml_export.tenant",
        tenant_id=str(tenant_id),
        demand_rows=len(demand_df),
        revenue_rows=len(revenue_df),
        customer_rows=len(customers_df),
    )
    return total


async def export_all(*, out_root: Path, fmt: str, min_orders: int) -> int:
    """Export every trainable tenant. Discovers tenants via the one cross-tenant query,
    then reads + writes each tenant-scoped. Returns the number of tenants exported."""
    engine = create_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            tenant_ids = await tenants_for_training(session, min_orders=min_orders)
            log.info("ml_export.start", tenants=len(tenant_ids), out=str(out_root), format=fmt)
            for tenant_id in tenant_ids:
                await export_tenant(session, tenant_id, out_root, fmt)
    finally:
        await engine.dispose()
    log.info("ml_export.done", tenants=len(tenant_ids))
    return len(tenant_ids)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export tenant training data for offline/Colab use.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output root directory")
    p.add_argument("--format", choices=("csv", "parquet"), default="csv", help="file format")
    p.add_argument(
        "--min-orders", type=int, default=1, help="only export tenants with >= this many orders"
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(export_all(out_root=args.out, fmt=args.format, min_orders=args.min_orders))


if __name__ == "__main__":
    main()
