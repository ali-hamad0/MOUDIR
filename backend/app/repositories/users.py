from uuid import UUID

from app.db.models import User
from app.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    model = User

    async def get_by_email(self, tenant_id: UUID, email: str) -> User | None:
        stmt = self._require_tenant_scope(tenant_id).where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
