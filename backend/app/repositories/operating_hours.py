from uuid import UUID

from app.db.models import OperatingHours
from app.repositories.base import TenantScopedRepository


class OperatingHoursRepository(TenantScopedRepository[OperatingHours]):
    model = OperatingHours

    async def get_by_day(self, tenant_id: UUID, day_of_week: int) -> OperatingHours | None:
        """One row per (tenant, day_of_week) — see uq_hours_tenant_day."""
        stmt = self._require_tenant_scope(tenant_id).where(
            OperatingHours.day_of_week == day_of_week
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
