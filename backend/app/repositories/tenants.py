from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant


class TenantRepository:
    """Root repository. Tenant has no tenant_id — it IS the tenant.

    Lookups here are deliberately NOT tenant-scoped (there is no outer scope),
    but they are narrow: by id, or by the destination whatsapp_number.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, id_: UUID) -> Tenant | None:
        result = await self._session.execute(select(Tenant).where(Tenant.id == id_))
        return result.scalar_one_or_none()

    async def get_by_whatsapp_number(self, number: str) -> Tenant | None:
        result = await self._session.execute(select(Tenant).where(Tenant.whatsapp_number == number))
        return result.scalar_one_or_none()

    async def add(self, tenant: Tenant) -> Tenant:
        self._session.add(tenant)
        await self._session.flush()
        return tenant
