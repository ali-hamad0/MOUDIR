"""Task 5.14 — the pgvector vector store + EmbeddingClient seam.

Proves the RAG plumbing: the stub embedder is deterministic and unit-norm; upsert
stores chunks and a re-embed REPLACES them (no duplicates/stale); search returns the
nearest chunks; and — the crux — search filters by tenant_id (and corpus) BEFORE the
similarity order-by, so tenant B's chunks NEVER surface for tenant A (constitution I,
the Wall for vector search). Runs against the real pgvector DB; the embedder is the
offline stub.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, VectorChunk
from app.infra.embeddings import StubEmbeddingClient
from app.infra.settings import Settings
from app.repositories.vector_chunks import VectorChunkRepository

DIM = 768


def _settings() -> Settings:
    return Settings.model_construct(embedding_mode="stub", embedding_dim=DIM)


def _embedder() -> StubEmbeddingClient:
    return StubEmbeddingClient(DIM)


@dataclass
class _Seed:
    tenant_id: UUID


async def _tenant(db: AsyncSession, name: str) -> UUID:
    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    return tenant.id


async def _embed_and_store(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    corpus: str,
    source_id: UUID,
    texts: list[str],
    content_hash: str | None = "h1",
) -> None:
    vecs = await _embedder().embed(texts)
    await VectorChunkRepository(db).upsert_chunks(
        tenant_id,
        corpus=corpus,
        source_type="product",
        source_id=source_id,
        content_hash=content_hash,
        chunks=list(zip(texts, vecs, strict=True)),
    )


# ── The stub embedder ────────────────────────────────────────────────────────


async def test_stub_is_deterministic_and_unit_norm() -> None:
    emb = _embedder()
    a = await emb.embed_one("بدكن تسليم لسن الفيل؟")
    b = await emb.embed_one("بدكن تسليم لسن الفيل؟")
    assert a == b  # deterministic
    assert len(a) == DIM
    assert round(float(np.linalg.norm(a)), 4) == 1.0
    # Different text → different vector.
    c = await emb.embed_one("شو أسعاركن؟")
    assert c != a


# ── upsert + search ──────────────────────────────────────────────────────────


async def test_search_returns_nearest_chunk(db_session: AsyncSession) -> None:
    tenant_id = await _tenant(db_session, "ShopA")
    source = uuid4()
    texts = ["منيوصّل لبيروت", "بنفتح من ٩ لـ٥", "أسعارنا منيحة"]
    await _embed_and_store(db_session, tenant_id, corpus="knowledge", source_id=source, texts=texts)

    # A query identical to a stored chunk is maximally similar → ranked first.
    qvec = await _embedder().embed_one("منيوصّل لبيروت")
    hits = await VectorChunkRepository(db_session).search(
        tenant_id, corpus="knowledge", query_embedding=qvec, k=3
    )
    assert hits[0].chunk_text == "منيوصّل لبيروت"


async def test_reembed_replaces_chunks(db_session: AsyncSession) -> None:
    tenant_id = await _tenant(db_session, "ShopA")
    source = uuid4()
    await _embed_and_store(
        db_session,
        tenant_id,
        corpus="knowledge",
        source_id=source,
        texts=["نص قديم"],
        content_hash="old",
    )
    # Re-embed the SAME source with new content → the old chunk is replaced, not added.
    await _embed_and_store(
        db_session,
        tenant_id,
        corpus="knowledge",
        source_id=source,
        texts=["نص جديد"],
        content_hash="new",
    )

    rows = (
        (
            await db_session.execute(
                select(VectorChunk).where(
                    VectorChunk.tenant_id == tenant_id, VectorChunk.source_id == source
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].chunk_text == "نص جديد"
    assert rows[0].content_hash == "new"


async def test_search_filters_by_corpus(db_session: AsyncSession) -> None:
    tenant_id = await _tenant(db_session, "ShopA")
    await _embed_and_store(
        db_session, tenant_id, corpus="knowledge", source_id=uuid4(), texts=["سياسة التوصيل"]
    )
    await _embed_and_store(
        db_session, tenant_id, corpus="bills", source_id=uuid4(), texts=["فاتورة قديمة"]
    )
    qvec = await _embedder().embed_one("سياسة التوصيل")
    hits = await VectorChunkRepository(db_session).search(
        tenant_id, corpus="knowledge", query_embedding=qvec, k=5
    )
    # Only knowledge-corpus chunks come back — the bills chunk is a different corpus.
    assert all(h.corpus == "knowledge" for h in hits)
    assert any(h.chunk_text == "سياسة التوصيل" for h in hits)


# ── The Wall: tenant filter BEFORE similarity ────────────────────────────────


async def test_search_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _tenant(db_session, "ShopA")
    b = await _tenant(db_session, "ShopB")
    # Both tenants store the EXACT same text → identical vectors. If search filtered
    # after similarity, B's chunk would tie/rank for A. It must not surface at all.
    await _embed_and_store(
        db_session, a, corpus="knowledge", source_id=uuid4(), texts=["منيوصّل لبيروت"]
    )
    await _embed_and_store(
        db_session, b, corpus="knowledge", source_id=uuid4(), texts=["منيوصّل لبيروت"]
    )

    qvec = await _embedder().embed_one("منيوصّل لبيروت")
    a_hits = await VectorChunkRepository(db_session).search(
        a, corpus="knowledge", query_embedding=qvec, k=10
    )
    assert len(a_hits) == 1  # only A's chunk, never B's identical one
    assert all(h.tenant_id == a for h in a_hits)

    # And B's own search sees only B's chunk.
    b_hits = await VectorChunkRepository(db_session).search(
        b, corpus="knowledge", query_embedding=qvec, k=10
    )
    assert len(b_hits) == 1
    assert all(h.tenant_id == b for h in b_hits)


async def test_delete_source_removes_chunks(db_session: AsyncSession) -> None:
    tenant_id = await _tenant(db_session, "ShopA")
    source = uuid4()
    await _embed_and_store(
        db_session, tenant_id, corpus="knowledge", source_id=source, texts=["x", "y"]
    )
    await VectorChunkRepository(db_session).delete_source(
        tenant_id, corpus="knowledge", source_type="product", source_id=source
    )
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(VectorChunk)
            .where(VectorChunk.tenant_id == tenant_id, VectorChunk.source_id == source)
        )
    ).scalar_one()
    assert n == 0
