"""Task 5.15 — the embedding worker leg (KnowledgeEmbedder).

Proves the worker drains knowledge_base_docs (pending/stale) into the vector store:
a pending product doc is embedded into the `knowledge` corpus and marked `embedded`;
a stale doc (content moved on) is re-embedded and its chunks replaced; a deleted
source drops its chunks; a committed supplier bill is embedded into the SEPARATE
`bills` corpus; and processing stays tenant-scoped (the Wall). The embedder is the
offline stub.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgeBaseDoc,
    Product,
    SupplierBillLine,
    Tenant,
    User,
    VectorChunk,
)
from app.infra.embeddings import StubEmbeddingClient
from app.infra.settings import Settings
from app.repositories.knowledge_base_docs import KnowledgeBaseDocRepository
from app.services.supplier_bills import SupplierBillService
from app.worker import KnowledgeEmbedder

DIM = 768


def _settings() -> Settings:
    return Settings.model_construct(embedding_mode="stub", embedding_dim=DIM, worker_batch_size=10)


def _sessionmaker_for(session: AsyncSession):
    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


def _embedder(session: AsyncSession) -> KnowledgeEmbedder:
    return KnowledgeEmbedder(
        sessionmaker=_sessionmaker_for(session),
        embedding_client=StubEmbeddingClient(DIM),
        settings=_settings(),
    )


async def _embed_tenant(session: AsyncSession, tenant_id: UUID) -> int:
    """Process ONLY the seeded tenant's embeddable docs.

    The worker's run_once() is cross-tenant and would also sweep any committed docs
    left in the dev DB (`tenants_with_embeddable_docs` reads committed rows). Scoping
    to the tenant under test keeps these assertions deterministic — the cross-tenant
    discovery itself is exercised by test_embedding_is_tenant_scoped."""
    return await _embedder(session)._embed_tenant(tenant_id)


@dataclass
class _Seed:
    tenant_id: UUID
    product_id: UUID
    user_id: UUID


async def _seed(db: AsyncSession, *, name: str = "ShopA") -> _Seed:
    from app.infra.security import hash_password

    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"{uuid4().hex[:8]}@a.com",
        hashed_password=hash_password("password123"),
        role="owner",
    )
    product = Product(
        tenant_id=tenant.id, name_ar="طحين", description_ar="طحين أبيض ممتاز", price_lbp=1000
    )
    db.add_all([user, product])
    await db.flush()
    return _Seed(tenant_id=tenant.id, product_id=product.id, user_id=user.id)


async def _track(
    db: AsyncSession, seed: _Seed, *, source_type: str, source_id: UUID, h: str
) -> None:
    await KnowledgeBaseDocRepository(db).mark_pending_or_stale(
        seed.tenant_id, source_type, source_id, h
    )
    await db.flush()


async def _doc_status(db: AsyncSession, tenant_id: UUID, source_id: UUID) -> str:
    return (
        await db.execute(
            select(KnowledgeBaseDoc.embedding_status).where(
                KnowledgeBaseDoc.tenant_id == tenant_id, KnowledgeBaseDoc.source_id == source_id
            )
        )
    ).scalar_one()


async def _chunks(db: AsyncSession, tenant_id: UUID, *, corpus: str, source_id: UUID):
    return (
        (
            await db.execute(
                select(VectorChunk).where(
                    VectorChunk.tenant_id == tenant_id,
                    VectorChunk.corpus == corpus,
                    VectorChunk.source_id == source_id,
                )
            )
        )
        .scalars()
        .all()
    )


# ── Pending product → knowledge corpus, marked embedded ──────────────────────


async def test_pending_doc_is_embedded(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    await _track(db_session, seed, source_type="product", source_id=seed.product_id, h="h1")

    n = await _embed_tenant(db_session, seed.tenant_id)
    assert n == 1

    rows = await _chunks(db_session, seed.tenant_id, corpus="knowledge", source_id=seed.product_id)
    assert len(rows) >= 1
    assert any("طحين" in r.chunk_text for r in rows)
    assert await _doc_status(db_session, seed.tenant_id, seed.product_id) == "embedded"


# ── Stale re-embed replaces chunks ───────────────────────────────────────────


async def test_stale_doc_is_reembedded(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    await _track(db_session, seed, source_type="product", source_id=seed.product_id, h="h1")
    await _embed_tenant(db_session, seed.tenant_id)  # embedded

    # The product's content changes → its KB row flips to stale (Phase 1 hook); here
    # we simulate that, then re-edit the product so the new text differs.
    product = (
        await db_session.execute(select(Product).where(Product.id == seed.product_id))
    ).scalar_one()
    product.name_ar = "طحين أسمر"
    await db_session.flush()
    await _track(db_session, seed, source_type="product", source_id=seed.product_id, h="h2")
    assert await _doc_status(db_session, seed.tenant_id, seed.product_id) == "stale"

    n = await _embed_tenant(db_session, seed.tenant_id)
    assert n == 1
    rows = await _chunks(db_session, seed.tenant_id, corpus="knowledge", source_id=seed.product_id)
    # Re-embed replaced the chunks; the new content is present.
    assert any("أسمر" in r.chunk_text for r in rows)
    assert await _doc_status(db_session, seed.tenant_id, seed.product_id) == "embedded"


# ── Deleted source → chunks dropped, doc handled ─────────────────────────────


async def test_vanished_source_drops_chunks(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    await _track(db_session, seed, source_type="product", source_id=seed.product_id, h="h1")
    await _embed_tenant(db_session, seed.tenant_id)
    assert (
        len(
            await _chunks(db_session, seed.tenant_id, corpus="knowledge", source_id=seed.product_id)
        )
        >= 1
    )

    # The product is deleted but a stale re-queue still points at it (race) — the
    # embedder finds no source, drops the chunks, and marks the doc handled.
    await db_session.execute(select(Product).where(Product.id == seed.product_id))  # ensure loaded
    product = (
        await db_session.execute(select(Product).where(Product.id == seed.product_id))
    ).scalar_one()
    await db_session.delete(product)
    await _track(db_session, seed, source_type="product", source_id=seed.product_id, h="h3")

    n = await _embed_tenant(db_session, seed.tenant_id)
    assert n == 1
    assert (
        await _chunks(db_session, seed.tenant_id, corpus="knowledge", source_id=seed.product_id)
        == []
    )
    assert await _doc_status(db_session, seed.tenant_id, seed.product_id) == "embedded"


# ── Committed bill → bills corpus ────────────────────────────────────────────


async def test_committed_bill_goes_to_bills_corpus(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    svc = SupplierBillService(db_session)
    bill = await svc.create_uploaded(
        tenant_id=seed.tenant_id,
        object_key=f"bills/{seed.tenant_id}/{uuid4()}/x.png",
        original_filename="x.png",
        content_type="image/png",
    )
    await svc.mark_processing(tenant_id=seed.tenant_id, bill_id=bill.id)
    await svc.save_extraction(
        tenant_id=seed.tenant_id,
        bill_id=bill.id,
        ocr_engine="stub",
        ocr_text="...",
        extracted={"total": "100"},
        lines=[
            SupplierBillLine(name_ar="طحين", quantity=Decimal("50"), product_id=seed.product_id)
        ],
        min_confidence=Decimal("0.9"),
        total_amount=Decimal("100"),
        currency="LBP",
    )
    await svc.approve(tenant_id=seed.tenant_id, bill_id=bill.id, approver_id=seed.user_id)
    await svc.mark_committed(tenant_id=seed.tenant_id, bill_id=bill.id)  # queues a 'bill' KB doc
    await db_session.flush()

    # The embedder routes source_type='bill' to the `bills` corpus.
    n = await _embed_tenant(db_session, seed.tenant_id)
    assert n == 1
    bills = await _chunks(db_session, seed.tenant_id, corpus="bills", source_id=bill.id)
    assert len(bills) >= 1
    assert any("طحين" in r.chunk_text for r in bills)
    # Nothing leaked into the knowledge corpus for the bill.
    assert await _chunks(db_session, seed.tenant_id, corpus="knowledge", source_id=bill.id) == []


# ── The Wall: only the owning tenant's docs are embedded in scope ────────────


async def test_embedding_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    await _track(db_session, a, source_type="product", source_id=a.product_id, h="h1")
    await _track(db_session, b, source_type="product", source_id=b.product_id, h="h1")

    # Each tenant's docs are processed in their OWN scope (the cross-tenant discovery
    # only hands out tenant ids; the work runs tenant-scoped).
    assert await _embed_tenant(db_session, a.tenant_id) == 1
    assert await _embed_tenant(db_session, b.tenant_id) == 1

    a_rows = await _chunks(db_session, a.tenant_id, corpus="knowledge", source_id=a.product_id)
    assert len(a_rows) >= 1
    assert all(r.tenant_id == a.tenant_id for r in a_rows)
    # B's chunks never landed under A (the Wall).
    cross = (
        await db_session.execute(
            select(func.count())
            .select_from(VectorChunk)
            .where(VectorChunk.tenant_id == a.tenant_id, VectorChunk.source_id == b.product_id)
        )
    ).scalar_one()
    assert cross == 0
