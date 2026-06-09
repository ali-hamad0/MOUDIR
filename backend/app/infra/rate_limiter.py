"""Per-tenant rate limiter backed by Redis (Phase 8, Task 8.1).

Strategy: token-bucket approximation using INCR + EXPIRE on a 1-minute
sliding window.  Key: `rate_limit:{tenant_id}:{window_minute}`.

Fail-open: if Redis is unreachable, the request is ALLOWED and a warning is
logged.  Observability is never more important than serving real traffic.

Limit = 0 means "no limit" for that tenant (explicit bypass, useful for
internal / high-tier tenants). The default RPM lives in Settings and can be
overridden per-tenant via the `rate_limit_rpm` business policy key.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.logging import get_logger

log = get_logger(__name__)

_POLICY_CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    current: int
    limit: int
    reset_at: datetime


class RateLimiter:
    def __init__(self, redis: Redis, default_rpm: int = 30) -> None:
        self._redis = redis
        self._default_rpm = default_rpm

    async def get_limit(self, tenant_id: UUID, db: AsyncSession) -> int:
        """Return the effective RPM limit for a tenant.

        Tries Redis cache first (60s TTL); on miss reads business_policies and
        back-fills the cache.  Redis failure returns the default without raising.
        """
        from app.repositories.business_policies import BusinessPolicyRepository

        cache_key = f"rate_limit_policy:{tenant_id}"
        try:
            cached = await self._redis.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception as exc:
            log.warning("rate_limiter.policy_cache_error", error=str(exc), tenant_id=str(tenant_id))
            return self._default_rpm

        policy = await BusinessPolicyRepository(db).get_by_key(tenant_id, "rate_limit_rpm")
        if policy is not None:
            try:
                limit = int(policy.value)
                # 0 is a valid explicit "no limit" — preserve it as-is
            except ValueError:
                limit = self._default_rpm
        else:
            limit = self._default_rpm

        try:
            await self._redis.set(cache_key, limit, ex=_POLICY_CACHE_TTL_SECONDS)
        except Exception as exc:
            log.warning("rate_limiter.policy_cache_write_error", error=str(exc))

        return limit

    async def check_and_increment(self, tenant_id: UUID, limit: int) -> RateLimitResult:
        """Increment the per-tenant, per-window counter and check the limit.

        Returns a RateLimitResult. On Redis failure returns allowed=True so a
        Redis outage never blocks legitimate traffic (fail-open).
        """
        now = int(time.time())
        window = now // 60
        reset_at = datetime.fromtimestamp((window + 1) * 60, tz=UTC)

        if limit == 0:
            return RateLimitResult(allowed=True, current=0, limit=0, reset_at=reset_at)

        key = f"rate_limit:{tenant_id}:{window}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                # First hit in this window — set TTL so stale keys don't pile up.
                await self._redis.expire(key, 60)
        except Exception as exc:
            log.warning(
                "rate_limiter.redis_error",
                error=str(exc),
                tenant_id=str(tenant_id),
            )
            return RateLimitResult(allowed=True, current=0, limit=limit, reset_at=reset_at)

        allowed = count <= limit
        if not allowed:
            log.warning(
                "rate_limit.exceeded",
                tenant_id=str(tenant_id),
                current=count,
                limit=limit,
                reset_at=reset_at.isoformat(),
            )
        return RateLimitResult(allowed=allowed, current=count, limit=limit, reset_at=reset_at)
