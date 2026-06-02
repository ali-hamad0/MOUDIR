from collections.abc import Sequence
from uuid import UUID

from app.db.models import Order, OrderItem
from app.repositories.base import TenantScopedRepository


class OrderRepository(TenantScopedRepository[Order]):
    """Tenant-scoped order reads/writes. Base CRUD covers Phase 2; the extra
    lookup below stays scoped through `_require_tenant_scope`."""

    model = Order

    async def list_for_customer(self, tenant_id: UUID, customer_id: UUID) -> Sequence[Order]:
        stmt = self._require_tenant_scope(tenant_id).where(Order.customer_id == customer_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()


class OrderItemRepository(TenantScopedRepository[OrderItem]):
    model = OrderItem
