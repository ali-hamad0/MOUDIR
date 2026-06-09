"""Business profile helpers — tenant defaults (Phase 8, Task 8.7)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BusinessPolicy
from app.repositories.business_policies import BusinessPolicyRepository

_DEFAULT_POLICIES: dict[str, str] = {
    "daily_llm_budget_usd": "0",
    "alert_webhook_url": "",
}


async def ensure_default_policies(session: AsyncSession, tenant_id: UUID) -> None:
    """Insert default cost-alert policy rows for a tenant if absent.

    Idempotent — safe to call on every tenant creation or at startup.
    The unique constraint (tenant_id, key) prevents duplicates.
    """
    repo = BusinessPolicyRepository(session)
    for key, default_value in _DEFAULT_POLICIES.items():
        if await repo.get_by_key(tenant_id, key) is None:
            session.add(BusinessPolicy(tenant_id=tenant_id, key=key, value=default_value))
    await session.flush()
