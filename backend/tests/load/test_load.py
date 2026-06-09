"""Task 8.3 — Load test: 100 concurrent requests across 10 tenants.

Run manually (requires Postgres at TEST_DATABASE_URL):
    pytest tests/load/test_load.py -m load -v

Excluded from the normal pytest suite (requires a running stack).

What it proves:
1. All 100 concurrent requests return HTTP 200.
2. Tenant isolation: every reply contains the tenant_id of the sender —
   no cross-tenant identity leak under concurrent load (The Wall).
3. Throughput: all 100 complete within TIMEOUT_SECONDS.

Architecture:
- Real Postgres: identity resolution (the Wall) runs against real rows.
- Fake Redis: RateLimiter backed by fakeredis (limit=0 = unlimited).
- Mock dispatcher: returns "tenant:<id>" so the test can assert isolation.
  The LLM and supervisor are never called.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from uuid import UUID

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import webhooks
from app.db.models import Customer, Tenant
from app.domain.identity import ResolvedIdentity
from app.infra.rate_limiter import RateLimiter

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://modir:modir@127.0.0.1:5432/modir",
)

N_TENANTS = 10
REQUESTS_PER_TENANT = 10  # N_TENANTS × REQUESTS_PER_TENANT = 100 total
TIMEOUT_SECONDS = 60.0
# Postgres default max_connections is 100; leave headroom for other sessions.
_MAX_CONCURRENT = 25

_TAG = "loadtest8"  # embedded in names so cleanup is precise


@dataclass
class _TenantSpec:
    tenant_id: UUID
    wa_number: str
    customer_phones: list[str] = field(default_factory=list)


# ── Mock dispatcher ───────────────────────────────────────────────────────────


class _MockDispatcher:
    """Echoes the resolved tenant_id — lets the test assert isolation."""

    async def dispatch(self, text: str, identity: ResolvedIdentity) -> str:
        return f"tenant:{identity.tenant.id}"


# ── Minimal test app ──────────────────────────────────────────────────────────


def _build_load_app(engine) -> FastAPI:
    """Minimal FastAPI with real identity resolution, fake Redis, mock dispatcher.

    No lifespan — state is set directly so no Vault / real LLM needed.
    """
    app = FastAPI()
    app.include_router(webhooks.router)
    app.state.db_engine = engine
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.redis = fake_redis
    app.state.rate_limiter = RateLimiter(fake_redis, default_rpm=0)  # 0 = unlimited
    app.state.dispatcher = _MockDispatcher()
    return app


# ── Module-scoped fixture: seed once, clean up after all load tests ───────────


@pytest_asyncio.fixture(scope="module")
async def load_tenants():
    """Seed N_TENANTS tenants each with REQUESTS_PER_TENANT customers.

    Uses a module-scoped engine so the seeded data is visible to every
    concurrent request (each opens its own session from the pool).
    Cleans up by deleting the seeded rows in FK order.
    """
    # NullPool: each request gets a fresh connection, no shared pool state.
    # Eliminates pool-level race conditions under high concurrent load.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tenant_ids: list[UUID] = []
    specs: list[_TenantSpec] = []

    # Pre-seed cleanup: remove any leftover rows from a previously interrupted run.
    async with session_factory() as session:
        async with session.begin():
            from sqlalchemy import select

            stale_rows = await session.execute(
                select(Tenant.id).where(Tenant.name.like(f"{_TAG}-%"))
            )
            stale_ids = [row[0] for row in stale_rows]
            if stale_ids:
                await session.execute(delete(Customer).where(Customer.tenant_id.in_(stale_ids)))
                await session.execute(delete(Tenant).where(Tenant.id.in_(stale_ids)))

    async with session_factory() as session:
        async with session.begin():
            for t in range(N_TENANTS):
                wa = f"+961980{t:02d}0000"
                tenant = Tenant(name=f"{_TAG}-T{t}", whatsapp_number=wa)
                session.add(tenant)
                await session.flush()
                tenant_ids.append(tenant.id)

                phones: list[str] = []
                for c in range(REQUESTS_PER_TENANT):
                    phone = f"+961970{t:02d}{c:04d}"
                    session.add(
                        Customer(
                            tenant_id=tenant.id,
                            phone_number=phone,
                            display_name=f"{_TAG}-C{t}-{c}",
                        )
                    )
                    phones.append(phone)

                await session.flush()
                specs.append(_TenantSpec(tenant_id=tenant.id, wa_number=wa, customer_phones=phones))

    yield engine, specs

    async with session_factory() as session:
        async with session.begin():
            await session.execute(delete(Customer).where(Customer.tenant_id.in_(tenant_ids)))
            await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))

    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _post_bounded(
    client: httpx.AsyncClient,
    payload: dict,
    sem: asyncio.Semaphore,
) -> httpx.Response:
    """Post one webhook request, bounded by the semaphore."""
    async with sem:
        return await client.post("/webhooks/whatsapp", json=payload)


async def _fire_all(app: FastAPI, payloads: list[dict]) -> list[httpx.Response]:
    """Fire all payloads concurrently, capped at _MAX_CONCURRENT active at once."""
    sem = asyncio.Semaphore(_MAX_CONCURRENT)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return list(await asyncio.gather(*[_post_bounded(client, p, sem) for p in payloads]))


# ── Load tests ────────────────────────────────────────────────────────────────


@pytest.mark.load
async def test_100_concurrent_requests_all_succeed(load_tenants):
    """All 100 webhook requests return HTTP 200."""
    engine, specs = load_tenants
    app = _build_load_app(engine)

    payloads = [
        {"to": spec.wa_number, "from": phone, "text": "بدي طلب"}
        for spec in specs
        for phone in spec.customer_phones
    ]

    responses = await _fire_all(app, payloads)

    failed = [(i, r.status_code) for i, r in enumerate(responses) if r.status_code != 200]
    assert not failed, f"{len(failed)} of 100 requests failed: {failed[:5]}"


@pytest.mark.load
async def test_100_concurrent_requests_tenant_isolation(load_tenants):
    """Every response carries the tenant_id of its sender — no cross-tenant leak.

    This is The Wall under concurrent load: identity resolution must never
    return a tenant_id other than the one the sender belongs to.
    """
    engine, specs = load_tenants
    app = _build_load_app(engine)

    cases: list[tuple[dict, str]] = [
        ({"to": spec.wa_number, "from": phone, "text": "كيف الأوضاع"}, str(spec.tenant_id))
        for spec in specs
        for phone in spec.customer_phones
    ]

    payloads = [payload for payload, _ in cases]
    responses = await _fire_all(app, payloads)

    violations: list[str] = []
    for i, (resp, (_, expected_id)) in enumerate(zip(responses, cases, strict=True)):
        if resp.status_code != 200:
            violations.append(f"Request {i}: HTTP {resp.status_code}")
            continue
        reply: str = resp.json()["reply"]
        if expected_id not in reply:
            violations.append(
                f"Request {i}: expected tenant {expected_id!r} in reply, got {reply!r}"
            )

    assert not violations, f"Isolation violated in {len(violations)} of 100 cases:\n" + "\n".join(
        violations[:10]
    )


@pytest.mark.load
async def test_100_concurrent_requests_throughput(load_tenants):
    """All 100 requests complete within TIMEOUT_SECONDS."""
    engine, specs = load_tenants
    app = _build_load_app(engine)

    payloads = [
        {"to": spec.wa_number, "from": phone, "text": "شو الطلبات"}
        for spec in specs
        for phone in spec.customer_phones
    ]

    start = time.monotonic()
    await _fire_all(app, payloads)
    elapsed = time.monotonic() - start

    assert (
        elapsed < TIMEOUT_SECONDS
    ), f"100 requests took {elapsed:.1f}s — limit is {TIMEOUT_SECONDS}s"
