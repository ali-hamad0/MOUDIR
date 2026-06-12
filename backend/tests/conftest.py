import asyncio
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest
import pytest_asyncio

if sys.platform == "win32":

    @pytest.fixture(scope="session")
    def event_loop_policy():
        # psycopg async (LangGraph checkpointer pool) cannot run on Windows'
        # default ProactorEventLoop — it hangs until PoolTimeout. Use the
        # selector loop for the whole suite; asyncpg works on both.
        return asyncio.WindowsSelectorEventLoopPolicy()


from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import (
    BusinessProfile,
    Product,
    Tenant,
    TenantOwner,
    User,
)
from app.infra.security import hash_password
from app.repositories.tenants import TenantRepository

# Tests run against a REAL Postgres (constitution: PG-specific types + pgvector
# must behave like prod — no sqlite). Defaults to the compose db exposed on
# localhost; CI overrides TEST_DATABASE_URL to point at its service container.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://modir:modir@127.0.0.1:5432/modir",
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A transactional session that is rolled back after each test.

    Every test runs inside an outer transaction bound to a single connection;
    repository commits flush within it, and the whole thing is discarded at the
    end — so tests never persist data and need no cleanup.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    connection = await engine.connect()
    trans = await connection.begin()
    session = AsyncSession(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()
        await engine.dispose()


@dataclass
class TenantBundle:
    """One fully-provisioned tenant for isolation tests."""

    tenant_id: UUID
    user_email: str
    owner_phone: str
    product_ids: list[UUID]


@dataclass
class TwoTenants:
    a: TenantBundle
    b: TenantBundle


async def _build_tenant(
    db: AsyncSession, *, name: str, wa: str, email: str, phone: str
) -> TenantBundle:
    tenant = await TenantRepository(db).add(Tenant(name=name, whatsapp_number=wa))
    db.add(
        User(
            tenant_id=tenant.id,
            email=email,
            hashed_password=hash_password("password123"),
            role="owner",
        )
    )
    db.add(
        TenantOwner(
            tenant_id=tenant.id,
            phone_number=phone,
            verification_status="verified",
        )
    )
    db.add(BusinessProfile(tenant_id=tenant.id, business_name=name))
    products = [
        Product(tenant_id=tenant.id, name_ar=f"{name}-منتج-{i}", price_lbp=1000 * (i + 1))
        for i in range(3)
    ]
    db.add_all(products)
    await db.flush()
    return TenantBundle(
        tenant_id=tenant.id,
        user_email=email,
        owner_phone=phone,
        product_ids=[p.id for p in products],
    )


@pytest_asyncio.fixture
async def two_tenants(db_session: AsyncSession) -> TwoTenants:
    """Two complete, distinct tenants (A and B), each with a verified owner, a
    dashboard user, and a 3-product catalog. The substrate for proving The Wall."""
    a = await _build_tenant(
        db_session, name="ShopA", wa="+961000000A1", email="a@a.com", phone="+961111111A"
    )
    b = await _build_tenant(
        db_session, name="ShopB", wa="+961000000B1", email="b@b.com", phone="+961111111B"
    )
    return TwoTenants(a=a, b=b)
