from uuid import UUID

from app.db.models import Customer
from app.repositories.base import TenantScopedRepository


class CustomerRepository(TenantScopedRepository[Customer]):
    model = Customer

    async def get_by_phone(self, tenant_id: UUID, phone_number: str) -> Customer | None:
        stmt = self._require_tenant_scope(tenant_id).where(Customer.phone_number == phone_number)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
