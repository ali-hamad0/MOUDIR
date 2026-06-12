"""Plan gating (Phase 11): Free vs Pro ($20/mo), enforced in code.

The plan difference is enforced HERE, at API entry points — never in prompts
(same philosophy as the Wall). Free is deliberately limited everywhere so the
upgrade is tangible:

  Free  → 10 products, 5 OCR bills/month, 15 chat messages/day,
          NO ML insights (demand/churn/anomaly), NO voice chat.
  Pro   → all of it, unlimited.

A tenant is effectively Pro only while the paid period covers today (the
7-day grace window keeps service while the renewal nag shows). An expired Pro
falls back to Free limits automatically — that is what makes the subscription
mean something without any scheduler.

Gate failures use HTTP 402 (Payment Required) with a machine-readable detail
("pro_required:<feature>" or "plan_limit:<feature>") so the dashboard can show
the Arabic upgrade prompt for exactly the right feature.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant
from app.infra.logging import get_logger
from app.repositories.tenants import TenantRepository
from app.services.billing import effective_subscription_status

log = get_logger(__name__)

PRO_PRICE_USD = Decimal("20.00")

FREE_MAX_PRODUCTS = 10
FREE_MAX_BILLS_PER_MONTH = 5
FREE_MAX_CHAT_MESSAGES_PER_DAY = 15

# HTTP 402 Payment Required — the dashboard maps these to upgrade prompts.
PAYMENT_REQUIRED = 402


def effective_plan(tenant: Tenant) -> str:
    """ "pro" only while the paid period covers today (grace included).

    "trialing" pro = plan_tier flipped to pro with NO paid period. The only way
    that state exists is the founder's manual plan change (a comp) — payments
    always set a period — so it counts as pro until the founder reverts it.
    """
    if tenant.plan_tier != "pro":
        return "free"
    if effective_subscription_status(tenant) in ("active", "past_due", "trialing"):
        return "pro"
    return "free"  # expired / suspended Pro behaves like Free


async def _load_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(401, "Tenant not found")
    return tenant


async def require_pro(db: AsyncSession, tenant_id: UUID, feature: str) -> None:
    """Block a Pro-only feature for effectively-Free tenants (402)."""
    tenant = await _load_tenant(db, tenant_id)
    if effective_plan(tenant) != "pro":
        log.info("plan_gate.blocked", tenant_id=str(tenant_id), feature=feature, kind="pro_only")
        raise HTTPException(PAYMENT_REQUIRED, f"pro_required:{feature}")


async def require_within_limit(
    db: AsyncSession, tenant_id: UUID, feature: str, current: int, free_limit: int
) -> None:
    """Block a Free-plan quota overrun (402). Pro is never limited."""
    tenant = await _load_tenant(db, tenant_id)
    if effective_plan(tenant) == "pro":
        return
    if current >= free_limit:
        log.info(
            "plan_gate.blocked",
            tenant_id=str(tenant_id),
            feature=feature,
            kind="limit",
            current=current,
            free_limit=free_limit,
        )
        raise HTTPException(PAYMENT_REQUIRED, f"plan_limit:{feature}")


def start_of_today() -> datetime:
    """Local midnight — the chat quota window."""
    return datetime.combine(datetime.now().date(), time.min)


def start_of_month() -> datetime:
    """First of the current month — the OCR-bill quota window."""
    today = datetime.now().date()
    return datetime.combine(today - timedelta(days=today.day - 1), time.min)
