from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Admin


class AdminRepository:
    """Founder/super-admin lookups. Like TenantRepository, this is deliberately
    NOT tenant-scoped — the admin identity sits ABOVE tenants. It must NEVER be
    given a path that reads or writes tenant-owned rows; that stays exclusively in
    the tenant-scoped repositories (constitution I). The founder touches tenant
    data only through dedicated, audited admin endpoints.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, id_: UUID) -> Admin | None:
        result = await self._session.execute(select(Admin).where(Admin.id == id_))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Admin | None:
        result = await self._session.execute(select(Admin).where(Admin.email == email))
        return result.scalar_one_or_none()

    async def add(self, admin: Admin) -> Admin:
        self._session.add(admin)
        await self._session.flush()
        return admin
