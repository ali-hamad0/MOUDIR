from uuid import UUID

from sqlalchemy import select

from app.db.models import User
from app.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    model = User

    async def get_by_email(self, tenant_id: UUID, email: str) -> User | None:
        stmt = self._require_tenant_scope(tenant_id).where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_activation_token(self, token: str) -> User | None:
        """Look a user up by their one-time activation token.

        Deliberately NOT tenant-scoped: during activation the owner has no JWT
        and no tenant context yet — the random token IS the secret and the scope.
        This is an above-login flow (like signup), the one sanctioned exception
        to tenant-scoped reads. It finds at most the single user holding this
        exact high-entropy token; it never exposes other tenants' data.
        """
        stmt = select(User).where(User.activation_token == token)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
