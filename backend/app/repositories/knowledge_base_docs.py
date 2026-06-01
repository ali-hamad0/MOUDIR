from uuid import UUID

from app.db.models import KnowledgeBaseDoc
from app.repositories.base import TenantScopedRepository


class KnowledgeBaseDocRepository(TenantScopedRepository[KnowledgeBaseDoc]):
    model = KnowledgeBaseDoc

    async def get_by_source(
        self, tenant_id: UUID, source_type: str, source_id: UUID
    ) -> KnowledgeBaseDoc | None:
        """One tracking row per (tenant, source_type, source_id) —
        see uq_kb_tenant_source."""
        stmt = self._require_tenant_scope(tenant_id).where(
            KnowledgeBaseDoc.source_type == source_type,
            KnowledgeBaseDoc.source_id == source_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
