"""Training-data reads for the ML layer (Phase 6, Task 6.3).

The three Phase 6 models train on history that already lives in the Phase 1–5 tables —
this repository is the ONE place that reads it, shaped for training and (crucially)
tenant-scoped. Every method here goes through `_require_tenant_scope` / an explicit
`tenant_id ==` filter, so the Wall holds during training exactly as it does in a
request (constitution I). Aggregation is done in SQL via `func` (GROUP BY / date_trunc),
never a hand-written raw-SQL string (the CI tenant-scoping guard bans raw SQL in
repositories — so even this comment avoids the literal the guard greps for).

What each read feeds:
  - daily_product_demand  -> the demand forecaster (Task 6.5): units sold per product
    per Beirut calendar day.
  - daily_revenue         -> the revenue-anomaly detector (Task 6.9): total LBP per day.
  - customer_order_history-> the churn classifier (Task 6.8): per-customer recency /
    frequency / monetary summary (the RFM the churn label + features are built from).

The ONE cross-tenant query (`tenants_for_training`) sits at module level, mirroring
`tenants_with_claimable_bills`: training has no request/JWT to scope it, so it discovers
WHICH tenants have enough history, then the trainer re-enters the Wall per tenant. Only
tenant IDs cross — never a tenant's rows.
"""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Row, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Date

from app.db.models import Customer, Order, OrderItem
from app.repositories.base import TenantScopedRepository

# Modir's shops are Lebanese; a "day" is a Beirut calendar day, not a UTC one. We bucket
# timestamps in the Asia/Beirut zone so a late-evening order counts on the right day
# (consistent with app/repositories/orders.py's beirut_day_start).
BEIRUT = "Asia/Beirut"


def _beirut_day(column):
    """SQL expression: the Beirut calendar date of a timestamptz column.

    `timezone('Asia/Beirut', ts)` shifts the stored UTC instant into Beirut local time;
    casting to Date drops the time. Done in SQL so the GROUP BY buckets correctly without
    pulling every row into Python.
    """
    return cast(func.timezone(BEIRUT, column), Date)


class TrainingDataRepository(TenantScopedRepository[Order]):
    """Tenant-scoped, training-shaped reads over the order/customer history.

    Bound to Order as the base model (the reads center on orders), but it also reads
    OrderItem and Customer — always with an explicit tenant filter on each table, and
    every JOIN scoped on BOTH sides (a cross-tenant JOIN is a Sev-1 leak, constitution I).
    Read-only: it never writes, so it owns no state transitions.
    """

    model = Order

    async def daily_product_demand(
        self, tenant_id: UUID, *, start: date | None = None, end: date | None = None
    ) -> Sequence[Row[tuple[UUID, date, int]]]:
        """Units sold per product per Beirut day: rows of (product_id, day, units).

        Sums OrderItem.quantity grouped by product and day. The OrderItem→Order JOIN is
        scoped on BOTH sides by tenant_id. Ordered by (product, day) so a per-product
        time series is contiguous for the feature builder. `start`/`end` (inclusive
        start, exclusive end) bound the window; omit them for all history.
        """
        day = _beirut_day(Order.created_at).label("day")
        stmt = (
            select(
                OrderItem.product_id,
                day,
                func.sum(OrderItem.quantity).label("units"),
            )
            .join(
                Order,
                (Order.id == OrderItem.order_id) & (Order.tenant_id == OrderItem.tenant_id),
            )
            .where(OrderItem.tenant_id == tenant_id)
            .group_by(OrderItem.product_id, day)
            .order_by(OrderItem.product_id, day)
        )
        stmt = self._bound_window(stmt, start, end)
        return (await self._session.execute(stmt)).all()

    async def daily_revenue(
        self, tenant_id: UUID, *, start: date | None = None, end: date | None = None
    ) -> Sequence[Row[tuple[date, int]]]:
        """Total revenue (LBP) per Beirut day: rows of (day, total_lbp), oldest first.

        Sums Order.total_lbp grouped by day — the daily series the anomaly detector
        scores. Tenant-scoped; a missing total counts as 0 (coalesced).
        """
        day = _beirut_day(Order.created_at).label("day")
        stmt = (
            select(day, func.coalesce(func.sum(Order.total_lbp), 0).label("total_lbp"))
            .where(Order.tenant_id == tenant_id)
            .group_by(day)
            .order_by(day)
        )
        stmt = self._bound_window(stmt, start, end)
        return (await self._session.execute(stmt)).all()

    async def customer_order_history(
        self, tenant_id: UUID
    ) -> Sequence[Row[tuple[UUID, int, datetime, datetime, int]]]:
        """Per-customer RFM summary: rows of (customer_id, order_count, first_order_at,
        last_order_at, total_spent_lbp).

        A LEFT JOIN so a customer with no orders still appears (count 0, nulls) — the
        churn model needs to see the never-returned too. The Order JOIN is scoped on
        BOTH sides by tenant_id. This is the raw material; the churn label ("at risk")
        and the recency/frequency/monetary features are derived from it in Task 6.4/6.8.
        """
        stmt = (
            select(
                Customer.id,
                func.count(Order.id).label("order_count"),
                func.min(Order.created_at).label("first_order_at"),
                func.max(Order.created_at).label("last_order_at"),
                func.coalesce(func.sum(Order.total_lbp), 0).label("total_spent_lbp"),
            )
            .outerjoin(
                Order,
                (Order.customer_id == Customer.id) & (Order.tenant_id == Customer.tenant_id),
            )
            .where(Customer.tenant_id == tenant_id)
            .group_by(Customer.id)
            .order_by(Customer.id)
        )
        return (await self._session.execute(stmt)).all()

    async def customer_orders(
        self, tenant_id: UUID, *, start: date | None = None, end: date | None = None
    ) -> Sequence[Row[tuple[UUID, date, int]]]:
        """One row PER ORDER: (customer_id, day, total_lbp), oldest first.

        The churn builder (Task 6.4) needs each order's day to split a customer's PAST
        orders (features) from the forward horizon window (label) — the aggregated
        `customer_order_history` can't do that, so this exposes the per-order grain.
        Tenant-scoped; a missing total counts as 0.
        """
        day = _beirut_day(Order.created_at).label("day")
        stmt = (
            select(Order.customer_id, day, func.coalesce(Order.total_lbp, 0).label("total_lbp"))
            .where(Order.tenant_id == tenant_id)
            .order_by(day)
        )
        stmt = self._bound_window(stmt, start, end)
        return (await self._session.execute(stmt)).all()

    @staticmethod
    def _bound_window(stmt, start: date | None, end: date | None):
        """Apply an inclusive-start / exclusive-end Beirut-day window to a statement
        whose grouped day column is `_beirut_day(Order.created_at)`."""
        day_expr = _beirut_day(Order.created_at)
        if start is not None:
            stmt = stmt.where(day_expr >= start)
        if end is not None:
            stmt = stmt.where(day_expr < end)
        return stmt


async def tenants_for_training(session: AsyncSession, *, min_orders: int = 1) -> Sequence[UUID]:
    """The distinct tenant ids with at least `min_orders` orders — i.e. enough history
    to train on.

    This is the ONE deliberately cross-tenant query in the training path (mirroring
    `tenants_with_claimable_bills`): training has no request/JWT to scope it, so it
    discovers WHICH tenants have data, then the trainer processes each one tenant-scoped
    through TrainingDataRepository. Only tenant IDS cross here — never a tenant's rows —
    and they are used purely to re-enter the Wall, never to read across it (constitution
    I). A module function, not a method on the scoped repo, to keep that boundary
    explicit.
    """
    stmt = (
        select(Order.tenant_id).group_by(Order.tenant_id).having(func.count(Order.id) >= min_orders)
    )
    return (await session.execute(stmt)).scalars().all()
