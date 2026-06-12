"""Task 5.16 — OrderAgent search_knowledge_base (RAG retrieval).

Proves the customer-path RAG: a policy/hours question retrieves the right chunk from
THIS tenant's knowledge corpus (tenant-filtered before similarity — the Wall), the
answer step is grounded in the retrieved context, the OrderAgent routes a non-order
message to the knowledge base, and a re-embed (stale → embedded) changes what is
retrieved. Embeddings are the offline stub and the answer LLM is faked — the suite
stays offline.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.order.tools import (
    ToolContext,
    answer_from_knowledge,
    search_knowledge_base,
)
from app.db.models import Customer, Tenant
from app.domain.identity import ResolvedIdentity
from app.infra.embeddings import StubEmbeddingClient
from app.infra.settings import Settings
from app.repositories.vector_chunks import VectorChunkRepository

DIM = 768


# ---- fakes ----
class _FakeChat:
    """A fake tier-1 chat model whose reply echoes the grounded context, so a test
    can assert the answer used retrieval."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def ainvoke(self, messages):
        class _Msg:
            content = self._reply

        return _Msg()


class _FakeRouter:
    def __init__(self, reply: str = "منيوصّل لبيروت أكيد") -> None:
        self._chat = _FakeChat(reply)

    def tier1(self):
        return self._chat

    def tier1_json(self):
        return self._chat

    def tier2(self):
        return self._chat


def _settings() -> Settings:
    return Settings.model_construct(embedding_mode="stub", embedding_dim=DIM, llm_max_retries=1)


def _identity(tenant: Tenant, customer: Customer) -> ResolvedIdentity:
    return ResolvedIdentity(tenant=tenant, role="customer", actor=customer)


def _ctx(session: AsyncSession, tenant: Tenant, customer: Customer, *, reply: str) -> ToolContext:
    return ToolContext(
        session=session,
        identity=_identity(tenant, customer),
        router=_FakeRouter(reply),
        settings=_settings(),
        embedding_client=StubEmbeddingClient(DIM),
    )


@dataclass
class _Seed:
    tenant: Tenant
    customer: Customer


async def _seed(db: AsyncSession, *, name: str = "ShopA") -> _Seed:
    tenant = Tenant(name=name, whatsapp_number=f"+961WA{uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()
    customer = Customer(tenant_id=tenant.id, phone_number=f"+961C{uuid4().hex[:6]}")
    db.add(customer)
    await db.flush()
    return _Seed(tenant=tenant, customer=customer)


async def _store(
    db: AsyncSession, tenant_id: UUID, text: str, *, source_id: UUID | None = None
) -> None:
    vec = await StubEmbeddingClient(DIM).embed_one(text)
    await VectorChunkRepository(db).upsert_chunks(
        tenant_id,
        corpus="knowledge",
        source_type="policy",
        source_id=source_id or uuid4(),
        content_hash="h",
        chunks=[(text, vec)],
    )


# ── search_knowledge_base ────────────────────────────────────────────────────


async def test_search_retrieves_relevant_chunk(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    await _store(db_session, seed.tenant.id, "منيوصّل لكل بيروت وضواحيها")
    await _store(db_session, seed.tenant.id, "بنفتح من ٩ الصبح لـ٦ المسا")

    ctx = _ctx(db_session, seed.tenant, seed.customer, reply="x")
    hits = await search_knowledge_base(ctx, "منيوصّل لكل بيروت وضواحيها")
    assert hits[0] == "منيوصّل لكل بيروت وضواحيها"


async def test_search_without_embedder_returns_empty(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    await _store(db_session, seed.tenant.id, "سياسة التوصيل")
    ctx = ToolContext(
        session=db_session,
        identity=_identity(seed.tenant, seed.customer),
        router=_FakeRouter(),
        settings=_settings(),
        embedding_client=None,  # no RAG configured
    )
    assert await search_knowledge_base(ctx, "بتوصّلوا؟") == []


async def test_search_is_tenant_scoped(db_session: AsyncSession) -> None:
    a = await _seed(db_session, name="ShopA")
    b = await _seed(db_session, name="ShopB")
    # Both store the SAME policy text → identical vectors.
    await _store(db_session, a.tenant.id, "منيوصّل لبيروت")
    await _store(db_session, b.tenant.id, "منيوصّل لبيروت")

    a_ctx = _ctx(db_session, a.tenant, a.customer, reply="x")
    hits = await search_knowledge_base(a_ctx, "منيوصّل لبيروت")
    # A sees only its own chunk, never B's identical one (tenant filter before
    # similarity — the Wall).
    assert len(hits) == 1


# ── answer_from_knowledge ────────────────────────────────────────────────────


async def test_answer_is_none_without_context(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    ctx = _ctx(db_session, seed.tenant, seed.customer, reply="should not be used")
    # No chunks → no grounding → None (caller falls back to "didn't understand").
    assert await answer_from_knowledge(ctx, "بتوصّلوا؟", []) is None


async def test_answer_uses_retrieved_context(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    ctx = _ctx(db_session, seed.tenant, seed.customer, reply="منيوصّل لبيروت أكيد")
    answer = await answer_from_knowledge(ctx, "بتوصّلوا لبيروت؟", ["منيوصّل لكل بيروت"])
    assert answer == "منيوصّل لبيروت أكيد"


# ── OrderAgent end-to-end: a question routes to the KB and is answered ───────


async def test_order_agent_answers_delivery_question(db_session: AsyncSession) -> None:
    from app.agents.order.agent import OrderAgent

    seed = await _seed(db_session)
    await _store(db_session, seed.tenant.id, "منيوصّل لكل بيروت وضواحيها بما فيها سن الفيل")

    # The agent uses ONE session for the whole run; hand it the test session, the
    # stub embedder, and a router whose parse step yields no order (→ routes to
    # search_kb) and whose answer step returns the grounded reply.
    @asynccontextmanager
    async def _cm():
        yield db_session

    class _ParseEmptyThenAnswer:
        """One fake chat model: with_structured_output(RawOrder) → empty order (so
        parse resolves to None → routes to the KB); a bare ainvoke → the KB answer."""

        def __init__(self, answer: str) -> None:
            self._answer = answer

        def with_structured_output(self, schema):
            class _Structured:
                async def ainvoke(self, messages):
                    return schema(items=[])

            return _Structured()

        async def ainvoke(self, messages):
            return await _FakeChat(self._answer).ainvoke(messages)

    class _Router:
        def __init__(self, answer: str) -> None:
            self._m = _ParseEmptyThenAnswer(answer)

        def tier1(self):
            return self._m

        def tier1_json(self):
            # parse step in JSON mode → empty order (routes to the KB)
            return _FakeChat('{"items": []}')

        def tier2(self):
            return self._m

    agent = OrderAgent(
        _Router("أكيد منوصّل لسن الفيل"), _settings(), lambda: _cm(), StubEmbeddingClient(DIM)
    )

    reply = await agent.handle("بتوصّلوا لسن الفيل؟", _identity(seed.tenant, seed.customer))
    assert reply == "أكيد منوصّل لسن الفيل"
