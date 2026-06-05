from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeBaseDoc
from app.repositories.base import TenantScopedRepository

# What the embedding worker (Task 5.15) (re-)embeds: rows queued since Phase 1/3
# (`pending`) and rows whose source content moved on (`stale`).
EMBEDDABLE_STATUSES = ("pending", "stale")


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

    async def list_embeddable(self, tenant_id: UUID, *, limit: int) -> Sequence[KnowledgeBaseDoc]:
        """KB docs the worker should (re-)embed for this tenant: `pending` + `stale`,
        oldest first (FIFO), tenant-scoped (Task 5.15)."""
        stmt = (
            self._require_tenant_scope(tenant_id)
            .where(KnowledgeBaseDoc.embedding_status.in_(EMBEDDABLE_STATUSES))
            .order_by(KnowledgeBaseDoc.created_at.asc())
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def mark_embedded(self, tenant_id: UUID, doc_id: UUID) -> None:
        """The worker embedded this doc: status → `embedded`, stamp embedded_at.
        Tenant-scoped (a foreign doc id matches nothing). Flushes; caller commits."""
        doc = await self.get(tenant_id, doc_id)
        if doc is None:
            return
        doc.embedding_status = "embedded"
        doc.embedded_at = datetime.now(UTC)
        await self._session.flush()


async def tenants_with_embeddable_docs(
    session: AsyncSession,
) -> Sequence[UUID]:
    """The distinct tenant ids with KB docs awaiting (re-)embedding.

    The ONE deliberately cross-tenant query in the embedding path (like
    tenants_with_claimable_bills): the worker has no JWT, so it discovers WHICH
    tenants have work, then processes each tenant-scoped. Only tenant IDS cross — never
    a tenant's content — and they are used purely to re-enter the Wall."""
    stmt = (
        select(KnowledgeBaseDoc.tenant_id)
        .where(KnowledgeBaseDoc.embedding_status.in_(EMBEDDABLE_STATUSES))
        .distinct()
    )
    return (await session.execute(stmt)).scalars().all()
