from uuid import UUID

from app.db.models import BusinessPolicy
from app.repositories.base import TenantScopedRepository


class BusinessPolicyRepository(TenantScopedRepository[BusinessPolicy]):
    model = BusinessPolicy

    async def get_by_key(self, tenant_id: UUID, key: str) -> BusinessPolicy | None:
        """One row per (tenant, key) — see uq_policy_tenant_key."""
        stmt = self._require_tenant_scope(tenant_id).where(BusinessPolicy.key == key)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
