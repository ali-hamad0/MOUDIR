from uuid import UUID

from app.db.models import BusinessProfile
from app.repositories.base import TenantScopedRepository


class BusinessProfileRepository(TenantScopedRepository[BusinessProfile]):
    model = BusinessProfile

    async def get_for_tenant(self, tenant_id: UUID) -> BusinessProfile | None:
        """There is exactly one profile row per tenant (uq_profile_tenant)."""
        result = await self._session.execute(self._require_tenant_scope(tenant_id))
        return result.scalar_one_or_none()
