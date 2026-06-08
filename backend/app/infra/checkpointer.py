"""LangGraph Postgres checkpointer helpers.

Task 7.2: conversation checkpoints persisted to Postgres so the owner
supervisor can resume a mid-flight run after a process restart.

Architecture notes (also in docs/DECISIONS.md Task 7.14):
- LangGraph's AsyncPostgresSaver manages its own tables via setup().
  These tables are NOT in our Alembic migrations — the library owns
  its schema, and setup() is idempotent (safe to call on every startup).
- Thread IDs MUST be prefixed with tenant_id (see make_thread_id).
  The Wall holds in LangGraph state only because of this prefix — a
  cross-tenant checkpoint collision is impossible when the prefix is unique
  per tenant. This module is the single source of thread ID construction;
  never inline the prefix in other code.
- The connection pool (psycopg3 AsyncConnectionPool) is separate from the
  SQLAlchemy asyncpg engine. Two drivers, one DB — acceptable because they
  serve different purposes (ORM vs LangGraph state), and keeping them
  separate avoids coupling the checkpoint lifecycle to SQLAlchemy sessions.
"""

from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


def make_thread_id(tenant_id: UUID, session_id: str) -> str:
    """Build a tenant-prefixed LangGraph checkpoint thread ID.

    This is the ONLY place thread IDs are formed. Callers pass the result
    directly to LangGraph's ``config["configurable"]["thread_id"]`` — never
    inline the prefix format outside this function.
    """
    return f"{tenant_id}:{session_id}"


def _psycopg_conn_str(database_url: str) -> str:
    """Convert a SQLAlchemy asyncpg URL to a psycopg3 connection string.

    SQLAlchemy uses ``postgresql+asyncpg://``;
    psycopg3 expects plain ``postgresql://``.
    """
    return str(database_url).replace("postgresql+asyncpg://", "postgresql://")


async def build_checkpointer(
    database_url: str,
) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Create and initialise the LangGraph Postgres checkpointer.

    Returns ``(checkpointer, pool)``.  The caller (lifespan) is responsible
    for calling ``await pool.close()`` on shutdown.

    ``checkpointer.setup()`` creates the LangGraph state tables if they do
    not yet exist — it is idempotent and safe to call on every startup.
    """
    conn_str = _psycopg_conn_str(str(database_url))
    # autocommit=True is required: AsyncPostgresSaver.setup() issues
    # CREATE INDEX CONCURRENTLY which Postgres forbids inside a transaction.
    pool = AsyncConnectionPool(conn_str, open=False, kwargs={"autocommit": True})
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    return checkpointer, pool
