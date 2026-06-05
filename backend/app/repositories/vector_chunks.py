from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select

from app.db.models import VectorChunk
from app.repositories.base import TenantScopedRepository


class VectorChunkRepository(TenantScopedRepository[VectorChunk]):
    """Tenant-scoped vector store for the RAG corpora.

    THE WALL FOR VECTOR SEARCH (constitution I, literal): `search` filters by
    tenant_id (and corpus) BEFORE the similarity order-by — never after. Filtering
    after similarity would rank another tenant's chunks into the candidate set first
    and only then drop them, which both leaks (timing/▷scoring) and is wrong; the
    WHERE clause restricts the rows the ANN/scan ever considers.
    """

    model = VectorChunk

    async def upsert_chunks(
        self,
        tenant_id: UUID,
        *,
        corpus: str,
        source_type: str,
        source_id: UUID,
        content_hash: str | None,
        chunks: Sequence[tuple[str, list[float]]],
    ) -> None:
        """Replace this source's chunks with a fresh set (re-embed semantics).

        Deletes the prior chunks for (tenant, corpus, source) then inserts the new
        ones in order, so a re-embed never leaves stale or duplicate chunks. All
        tenant-scoped — the delete and inserts are bound to this tenant. `chunks` is
        a list of (chunk_text, embedding) in order. Flushes; the caller commits.
        """
        await self._session.execute(
            delete(VectorChunk).where(
                VectorChunk.tenant_id == tenant_id,
                VectorChunk.corpus == corpus,
                VectorChunk.source_type == source_type,
                VectorChunk.source_id == source_id,
            )
        )
        for index, (text, embedding) in enumerate(chunks):
            self._session.add(
                VectorChunk(
                    tenant_id=tenant_id,
                    corpus=corpus,
                    source_type=source_type,
                    source_id=source_id,
                    content_hash=content_hash,
                    chunk_index=index,
                    chunk_text=text,
                    embedding=embedding,
                )
            )
        await self._session.flush()

    async def delete_source(
        self, tenant_id: UUID, *, corpus: str, source_type: str, source_id: UUID
    ) -> None:
        """Remove all chunks for a source (e.g. when the product is deleted),
        tenant-scoped."""
        await self._session.execute(
            delete(VectorChunk).where(
                VectorChunk.tenant_id == tenant_id,
                VectorChunk.corpus == corpus,
                VectorChunk.source_type == source_type,
                VectorChunk.source_id == source_id,
            )
        )
        await self._session.flush()

    async def search(
        self,
        tenant_id: UUID,
        *,
        corpus: str,
        query_embedding: list[float],
        k: int,
    ) -> Sequence[VectorChunk]:
        """Top-k most similar chunks for this tenant + corpus, nearest first.

        The WHERE clause (tenant_id + corpus) is applied BEFORE the cosine-distance
        order-by — the Wall for vector search. Cosine distance pairs with the
        unit-norm vectors the embedding clients produce.
        """
        stmt = (
            select(VectorChunk)
            .where(
                VectorChunk.tenant_id == tenant_id,
                VectorChunk.corpus == corpus,
            )
            .order_by(VectorChunk.embedding.cosine_distance(query_embedding))
            .limit(k)
        )
        return (await self._session.execute(stmt)).scalars().all()
