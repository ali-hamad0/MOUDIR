from uuid import UUID

from app.db.models import TenantOwner
from app.repositories.base import TenantScopedRepository


class TenantOwnerRepository(TenantScopedRepository[TenantOwner]):
    model = TenantOwner

    async def get_by_phone(self, tenant_id: UUID, phone_number: str) -> TenantOwner | None:
        stmt = self._require_tenant_scope(tenant_id).where(TenantOwner.phone_number == phone_number)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
