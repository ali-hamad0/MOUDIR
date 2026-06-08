"""Task 7.2 — Postgres checkpointer helpers.

Unit tests cover make_thread_id and _psycopg_conn_str (pure functions, no DB).
The integration test verifies cross-tenant checkpoint isolation against a real
Postgres instance; it is skipped when DATABASE_URL is not set in the environment.

The full "kill and resume" test lives in Task 7.11 (test_checkpoint_resume.py).
"""

import os
import uuid

import pytest

from app.infra.checkpointer import _psycopg_conn_str, make_thread_id

# ---------------------------------------------------------------------------
# make_thread_id — pure, no DB
# ---------------------------------------------------------------------------


class TestMakeThreadId:
    def test_format_is_tenant_colon_session(self):
        tenant = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
        result = make_thread_id(tenant, "sess-123")
        assert result == "aaaaaaaa-0000-0000-0000-000000000001:sess-123"

    def test_always_starts_with_tenant_id(self):
        tenant = uuid.uuid4()
        session = str(uuid.uuid4())
        assert make_thread_id(tenant, session).startswith(str(tenant))

    def test_different_tenants_same_session_produce_different_ids(self):
        session = "same-session"
        a = make_thread_id(uuid.uuid4(), session)
        b = make_thread_id(uuid.uuid4(), session)
        assert a != b

    def test_same_tenant_different_sessions_produce_different_ids(self):
        tenant = uuid.uuid4()
        a = make_thread_id(tenant, "session-1")
        b = make_thread_id(tenant, "session-2")
        assert a != b

    def test_deterministic_for_same_inputs(self):
        tenant = uuid.uuid4()
        session = "abc"
        assert make_thread_id(tenant, session) == make_thread_id(tenant, session)


# ---------------------------------------------------------------------------
# _psycopg_conn_str — pure, no DB
# ---------------------------------------------------------------------------


class TestPsycopgConnStr:
    def test_strips_asyncpg_scheme(self):
        url = "postgresql+asyncpg://user:pass@localhost:5432/modir"
        assert _psycopg_conn_str(url) == "postgresql://user:pass@localhost:5432/modir"

    def test_plain_postgresql_url_unchanged(self):
        url = "postgresql://user:pass@localhost:5432/modir"
        assert _psycopg_conn_str(url) == url

    def test_result_does_not_contain_asyncpg(self):
        url = "postgresql+asyncpg://x:x@db:5432/modir"
        assert "asyncpg" not in _psycopg_conn_str(url)


# ---------------------------------------------------------------------------
# Integration — real Postgres required (skipped in offline CI)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_checkpoint_cross_tenant_isolation():
    """Checkpoints under tenant A's thread_id are not visible under tenant B's.

    Proves The Wall holds at the LangGraph checkpoint layer: using different
    tenant-prefixed thread IDs prevents cross-tenant state leakage even when
    the same session UUID is reused.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        pytest.skip("DATABASE_URL not set — integration test skipped")

    conn_str = _psycopg_conn_str(db_url)
    pool = AsyncConnectionPool(conn_str, open=False)
    await pool.open()
    try:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()

        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        session = str(uuid.uuid4())

        thread_a = make_thread_id(tenant_a, session)
        thread_b = make_thread_id(tenant_b, session)

        # Write a minimal checkpoint under tenant A
        config_a = {"configurable": {"thread_id": thread_a, "checkpoint_ns": ""}}
        from langgraph.checkpoint.base import empty_checkpoint

        checkpoint = empty_checkpoint()
        await checkpointer.aput(config_a, checkpoint, {}, {})

        # Tenant A can read its own checkpoint
        result_a = await checkpointer.aget_tuple(config_a)
        assert result_a is not None, "Tenant A's checkpoint should be readable"

        # Tenant B's thread_id differs — no result
        config_b = {"configurable": {"thread_id": thread_b, "checkpoint_ns": ""}}
        result_b = await checkpointer.aget_tuple(config_b)
        assert result_b is None, "Tenant B must not see Tenant A's checkpoint"
    finally:
        await pool.close()
