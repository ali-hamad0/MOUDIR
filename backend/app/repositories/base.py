from collections.abc import Sequence
from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class TenantScopedRepository(Generic[ModelT]):
    """Base repository. EVERY method requires tenant_id and filters by it.

    The Wall (constitution I) is enforced HERE, in code — never in a prompt and
    never trusted from a JWT claim. A model without a `tenant_id` column cannot
    be used with this base; that is intentional.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _require_tenant_scope(self, tenant_id: UUID):
        """Return the base SELECT already filtered by tenant_id.

        Subclasses build on this; they never start a query without it.
        """
        if tenant_id is None:
            # Defensive: a None tenant_id is a programming error, not a query.
            raise ValueError("tenant_id is required on every repository query")
        return select(self.model).where(self.model.tenant_id == tenant_id)

    async def get(self, tenant_id: UUID, id_: UUID) -> ModelT | None:
        stmt = self._require_tenant_scope(tenant_id).where(self.model.id == id_)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, tenant_id: UUID) -> Sequence[ModelT]:
        result = await self._session.execute(self._require_tenant_scope(tenant_id))
        return result.scalars().all()

    async def add(self, tenant_id: UUID, instance: ModelT) -> ModelT:
        # Force the row's tenant_id to the caller's scope — never trust the
        # instance to carry the right one.
        instance.tenant_id = tenant_id
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def delete(self, tenant_id: UUID, id_: UUID) -> bool:
        obj = await self.get(tenant_id, id_)
        if obj is None:
            return False
        await self._session.delete(obj)
        await self._session.flush()
        return True
