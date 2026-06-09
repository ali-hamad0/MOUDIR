"""Task 8.1 — per-tenant rate limiter (Redis token bucket).

Tests cover the RateLimiter class directly using fakeredis (offline, no real
Redis required).  Four assertions that match the Task 8.1 DoD:
  (a) tenant hitting limit → allowed=False returned; 429 from the webhook
  (b) two tenants have fully independent counters
  (c) counter in window N does not affect window N+1 (time-mocked)
  (d) policy row with value "0" → limit treated as unlimited, always allowed
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fakeredis.aioredis import FakeRedis

from app.infra.rate_limiter import RateLimiter

# ── helpers ─────────────────────────────────────────────────────────────────


def _fake_redis() -> FakeRedis:
    return FakeRedis()


# ── (a) tenant hits limit → check_and_increment returns allowed=False ────────


@pytest.mark.asyncio
async def test_limit_exceeded_returns_not_allowed() -> None:
    limiter = RateLimiter(_fake_redis(), default_rpm=3)
    tenant_id = uuid4()

    for i in range(3):
        result = await limiter.check_and_increment(tenant_id, limit=3)
        assert result.allowed, f"expected allowed on call {i + 1}"
        assert result.current == i + 1

    over_limit = await limiter.check_and_increment(tenant_id, limit=3)
    assert not over_limit.allowed
    assert over_limit.current == 4
    assert over_limit.limit == 3


# ── (b) two tenants have independent counters ────────────────────────────────


@pytest.mark.asyncio
async def test_two_tenants_independent_counters() -> None:
    redis = _fake_redis()
    limiter = RateLimiter(redis, default_rpm=2)
    tenant_a = uuid4()
    tenant_b = uuid4()

    # Exhaust tenant A
    for _ in range(2):
        await limiter.check_and_increment(tenant_a, limit=2)

    over_a = await limiter.check_and_increment(tenant_a, limit=2)
    assert not over_a.allowed, "tenant_a should be over limit"

    # tenant_b counter is independent — still allowed
    first_b = await limiter.check_and_increment(tenant_b, limit=2)
    assert first_b.allowed, "tenant_b counter must be independent of tenant_a"
    assert first_b.current == 1


# ── (c) counter in window N does not affect window N+1 ──────────────────────


@pytest.mark.asyncio
async def test_counter_resets_in_next_window() -> None:
    redis = _fake_redis()
    limiter = RateLimiter(redis, default_rpm=1)
    tenant_id = uuid4()

    # Window 1: first call allowed, second not
    with patch("app.infra.rate_limiter.time.time", return_value=1_000_000.0):
        result = await limiter.check_and_increment(tenant_id, limit=1)
        assert result.allowed
        over = await limiter.check_and_increment(tenant_id, limit=1)
        assert not over.allowed

    # Window 2 (a different minute): fresh counter → allowed again
    with patch("app.infra.rate_limiter.time.time", return_value=1_000_060.0):
        new_window = await limiter.check_and_increment(tenant_id, limit=1)
        assert new_window.allowed
        assert new_window.current == 1


# ── (d) policy value "0" → unlimited bypass ──────────────────────────────────


@pytest.mark.asyncio
async def test_limit_zero_means_unlimited() -> None:
    limiter = RateLimiter(_fake_redis(), default_rpm=1)
    tenant_id = uuid4()

    for _ in range(200):
        result = await limiter.check_and_increment(tenant_id, limit=0)
        assert result.allowed


# ── get_limit: no policy row → returns default ───────────────────────────────


@pytest.mark.asyncio
async def test_get_limit_no_policy_row_returns_default() -> None:
    redis = _fake_redis()
    limiter = RateLimiter(redis, default_rpm=30)
    tenant_id = uuid4()
    mock_db = AsyncMock()

    with patch(
        "app.repositories.business_policies.BusinessPolicyRepository.get_by_key",
        new_callable=AsyncMock,
        return_value=None,
    ):
        limit = await limiter.get_limit(tenant_id, mock_db)

    assert limit == 30


# ── get_limit: policy value "0" → 0 preserved (unlimited) ───────────────────


@pytest.mark.asyncio
async def test_get_limit_policy_zero_preserved() -> None:
    redis = _fake_redis()
    limiter = RateLimiter(redis, default_rpm=30)
    tenant_id = uuid4()
    mock_db = AsyncMock()

    mock_policy = MagicMock()
    mock_policy.value = "0"

    with patch(
        "app.repositories.business_policies.BusinessPolicyRepository.get_by_key",
        new_callable=AsyncMock,
        return_value=mock_policy,
    ):
        limit = await limiter.get_limit(tenant_id, mock_db)

    assert limit == 0


# ── get_limit: policy value "60" → 60 returned ───────────────────────────────


@pytest.mark.asyncio
async def test_get_limit_custom_policy_value() -> None:
    redis = _fake_redis()
    limiter = RateLimiter(redis, default_rpm=30)
    tenant_id = uuid4()
    mock_db = AsyncMock()

    mock_policy = MagicMock()
    mock_policy.value = "60"

    with patch(
        "app.repositories.business_policies.BusinessPolicyRepository.get_by_key",
        new_callable=AsyncMock,
        return_value=mock_policy,
    ):
        limit = await limiter.get_limit(tenant_id, mock_db)

    assert limit == 60


# ── get_limit: policy value cached in Redis on first DB hit ──────────────────


@pytest.mark.asyncio
async def test_get_limit_caches_result_in_redis() -> None:
    redis = _fake_redis()
    limiter = RateLimiter(redis, default_rpm=30)
    tenant_id = uuid4()
    mock_db = AsyncMock()

    mock_policy = MagicMock()
    mock_policy.value = "45"

    with patch(
        "app.repositories.business_policies.BusinessPolicyRepository.get_by_key",
        new_callable=AsyncMock,
        return_value=mock_policy,
    ) as mock_get:
        # First call: DB hit
        limit1 = await limiter.get_limit(tenant_id, mock_db)
        # Second call: should read from Redis cache, no DB hit
        limit2 = await limiter.get_limit(tenant_id, mock_db)

    assert limit1 == 45
    assert limit2 == 45
    # DB was called exactly once; second call used the Redis cache
    assert mock_get.call_count == 1


# ── fail-open: Redis down does not block traffic ──────────────────────────────


@pytest.mark.asyncio
async def test_redis_error_fails_open() -> None:
    """A Redis outage must allow traffic through, not block it."""
    limiter = RateLimiter(_fake_redis(), default_rpm=1)
    tenant_id = uuid4()

    with patch.object(limiter._redis, "incr", side_effect=ConnectionError("redis down")):
        result = await limiter.check_and_increment(tenant_id, limit=1)

    assert result.allowed, "Redis failure must fail open — never block requests"
