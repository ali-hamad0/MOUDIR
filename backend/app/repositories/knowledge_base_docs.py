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

    async def mark_pending_or_stale(
        self,
        tenant_id: UUID,
        source_type: str,
        source_id: UUID,
        content_hash: str,
    ) -> KnowledgeBaseDoc | None:
        """Hook called after a product/policy/hours write so Phase 5 knows what
        to (re-)embed.

        - No tracking row yet  -> insert one as ``pending``.
        - Row exists, hash changed -> mark ``stale`` (content moved on).
        - Row exists, hash unchanged -> no-op (don't needlessly re-queue).

        Returns the row that changed, or ``None`` when nothing did.
        """
        existing = await self.get_by_source(tenant_id, source_type, source_id)
        if existing is None:
            return await self.add(
                tenant_id,
                KnowledgeBaseDoc(
                    source_type=source_type,
                    source_id=source_id,
                    content_hash=content_hash,
                    embedding_status="pending",
                ),
            )

        if existing.content_hash != content_hash:
            existing.content_hash = content_hash
            existing.embedding_status = "stale"
            await self._session.flush()
            return existing

        return None

    async def delete_by_source(self, tenant_id: UUID, source_type: str, source_id: UUID) -> bool:
        """Remove the tracking row when its source is deleted, so no stale entry
        lingers. Returns False if there was nothing to delete."""
        existing = await self.get_by_source(tenant_id, source_type, source_id)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True
