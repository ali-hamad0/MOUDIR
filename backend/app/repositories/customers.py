from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Row, func, select

from app.db.models import Customer, Order
from app.repositories.base import TenantScopedRepository


class CustomerRepository(TenantScopedRepository[Customer]):
    model = Customer

    async def get_by_phone(self, tenant_id: UUID, phone_number: str) -> Customer | None:
        stmt = self._require_tenant_scope(tenant_id).where(Customer.phone_number == phone_number)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count(self, tenant_id: UUID) -> int:
        stmt = select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_with_stats(self, tenant_id: UUID, *, limit: int, offset: int) -> Sequence[Row]:
        """One page of this tenant's customers with per-customer order stats:
        order count, total spent (LBP + USD), and last order time.

        A LEFT JOIN so customers with no orders still appear (totals zero/null).
        The JOIN to orders is scoped on BOTH sides by tenant_id — a cross-tenant
        JOIN is a Sev-1 leak (constitution I). Sorted by most recent activity.
        """
        stmt = (
            select(
                Customer,
                func.count(Order.id).label("order_count"),
                func.coalesce(func.sum(Order.total_lbp), 0).label("total_spent_lbp"),
                func.sum(Order.total_usd).label("total_spent_usd"),
                func.max(Order.created_at).label("last_order_at"),
            )
            .outerjoin(
                Order,
                (Order.customer_id == Customer.id) & (Order.tenant_id == Customer.tenant_id),
            )
            .where(Customer.tenant_id == tenant_id)
            .group_by(Customer.id)
            .order_by(
                func.max(Order.created_at).desc().nullslast(),
                Customer.first_seen_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).all()
