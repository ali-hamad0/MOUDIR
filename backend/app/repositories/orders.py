from collections.abc import Sequence
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Row, func, select

from app.db.models import Customer, Order, OrderItem
from app.repositories.base import TenantScopedRepository

# Modir serves Lebanese shops; "today" is a Beirut calendar day, not UTC.
BEIRUT_TZ = ZoneInfo("Asia/Beirut")


def beirut_day_start(now: datetime | None = None) -> datetime:
    """Midnight (Beirut) of the current day, as a timezone-aware datetime."""
    now_beirut = (now or datetime.now(BEIRUT_TZ)).astimezone(BEIRUT_TZ)
    return datetime.combine(now_beirut.date(), time.min, tzinfo=BEIRUT_TZ)


class OrderRepository(TenantScopedRepository[Order]):
    """Tenant-scoped order reads/writes. Base CRUD covers Phase 2; the extra
    lookups below stay scoped through `_require_tenant_scope`."""

    model = Order

    async def list_for_customer(self, tenant_id: UUID, customer_id: UUID) -> Sequence[Order]:
        stmt = self._require_tenant_scope(tenant_id).where(Order.customer_id == customer_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_today(self, tenant_id: UUID, *, since: datetime | None = None) -> int:
        """Count this tenant's orders since Beirut midnight (for pagination)."""
        day_start = since or beirut_day_start()
        stmt = (
            select(func.count())
            .select_from(Order)
            .where(Order.tenant_id == tenant_id, Order.created_at >= day_start)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_today(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
        since: datetime | None = None,
    ) -> Sequence[Row[tuple[Order, str | None]]]:
        """One page of today's orders, newest first, with the customer display
        name joined in. The JOIN to customers is scoped on BOTH sides by
        tenant_id (constitution I: a cross-tenant JOIN is a Sev-1 leak)."""
        day_start = since or beirut_day_start()
        stmt = (
            select(Order, Customer.display_name)
            .join(
                Customer,
                (Customer.id == Order.customer_id) & (Customer.tenant_id == Order.tenant_id),
            )
            .where(Order.tenant_id == tenant_id, Order.created_at >= day_start)
            .order_by(Order.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).all()

    async def items_for_orders(
        self, tenant_id: UUID, order_ids: Sequence[UUID]
    ) -> Sequence[OrderItem]:
        """All order_items for the given orders, tenant-scoped, in ONE query
        (batched to avoid an N+1 over the order page)."""
        if not order_ids:
            return []
        stmt = self._require_tenant_scope_items(tenant_id).where(OrderItem.order_id.in_(order_ids))
        return (await self._session.execute(stmt)).scalars().all()

    def _require_tenant_scope_items(self, tenant_id: UUID):
        if tenant_id is None:
            raise ValueError("tenant_id is required on every repository query")
        return select(OrderItem).where(OrderItem.tenant_id == tenant_id)

    # ── Dashboard aggregates (Phase 10) ────────────────────────────────────
    # All grouped in Beirut-local days (the shop's calendar), all tenant-scoped.

    async def daily_totals(
        self, tenant_id: UUID, *, days: int, now: datetime | None = None
    ) -> Sequence[Row]:
        """(day, orders, revenue_lbp) per Beirut day over the trailing window.
        Days with no orders simply don't appear — the API fills the gaps so the
        chart x-axis is continuous."""
        window_start = beirut_day_start(now) - timedelta(days=days - 1)
        day = func.date(func.timezone("Asia/Beirut", Order.created_at)).label("day")
        stmt = (
            select(
                day,
                func.count().label("orders"),
                func.coalesce(func.sum(Order.total_lbp), 0).label("revenue_lbp"),
            )
            .where(Order.tenant_id == tenant_id, Order.created_at >= window_start)
            .group_by(day)
            .order_by(day)
        )
        return (await self._session.execute(stmt)).all()

    async def top_products(
        self, tenant_id: UUID, *, days: int, k: int, now: datetime | None = None
    ) -> Sequence[Row]:
        """(name, units) for the k best-selling products over the trailing
        window, by snapshotted item name (a later catalog rename can't rewrite
        history). The JOIN to orders is scoped on BOTH sides (constitution I)."""
        window_start = beirut_day_start(now) - timedelta(days=days - 1)
        stmt = (
            select(
                OrderItem.name_ar_snapshot.label("name"),
                func.sum(OrderItem.quantity).label("units"),
            )
            .join(
                Order,
                (Order.id == OrderItem.order_id) & (Order.tenant_id == OrderItem.tenant_id),
            )
            .where(OrderItem.tenant_id == tenant_id, Order.created_at >= window_start)
            .group_by(OrderItem.name_ar_snapshot)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(k)
        )
        return (await self._session.execute(stmt)).all()

    async def status_counts(
        self, tenant_id: UUID, *, days: int, now: datetime | None = None
    ) -> Sequence[Row]:
        """(status, count) over the trailing window — the donut's slices."""
        window_start = beirut_day_start(now) - timedelta(days=days - 1)
        stmt = (
            select(Order.status, func.count().label("count"))
            .where(Order.tenant_id == tenant_id, Order.created_at >= window_start)
            .group_by(Order.status)
            .order_by(func.count().desc())
        )
        return (await self._session.execute(stmt)).all()


class OrderItemRepository(TenantScopedRepository[OrderItem]):
    model = OrderItem
