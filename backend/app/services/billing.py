"""Founder billing: manual subscription payments + plan changes.

Lebanon-market v1: there is no card gateway. The owner pays the founder
out-of-band (Whish / OMT / cash / bank card transfer), the founder records the
payment here, and the tenant's paid period extends. Expiry is derived at read
time from current_period_end — no scheduler. Enforcement stays the existing
suspend machinery (services/tenant_admin.py): the founder sees past-due shops
on the admin dashboard and suspends with a reason.

Both actions are Level-3 (founder-initiated only, constitution V): never
reachable from an agent, always audited.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Admin, SubscriptionPayment, Tenant
from app.infra.logging import get_logger
from app.repositories.subscription_payments import SubscriptionPaymentRepository
from app.repositories.tenants import TenantRepository
from app.services.audit import AuditService

log = get_logger(__name__)

PLAN_TIERS = {"free", "pro"}
PAYMENT_METHODS = {"whish", "omt", "cash", "card", "other"}
# A paid month is 30 days — simple and predictable for cash billing.
PERIOD_DAYS_PER_MONTH = 30
# Days after current_period_end before "past_due" becomes "expired".
GRACE_DAYS = 7


def effective_subscription_status(tenant: Tenant, today: date | None = None) -> str:
    """Derive the display/billing status from stored fields.

    trialing → never paid; active → paid period covers today; past_due →
    expired within the grace window; expired → past grace. Suspension
    (is_active=False) overrides everything.
    """
    if not tenant.is_active:
        return "suspended"
    if tenant.current_period_end is None:
        return tenant.subscription_status  # "trialing" until the first payment
    today = today or date.today()
    if tenant.current_period_end >= today:
        return "active"
    if (today - tenant.current_period_end).days <= GRACE_DAYS:
        return "past_due"
    return "expired"


async def record_payment(
    db: AsyncSession,
    *,
    admin: Admin,
    tenant_id: UUID,
    amount_usd: Decimal,
    method: str,
    months: int = 1,
    note: str | None = None,
    plan_tier: str | None = None,
) -> tuple[Tenant, SubscriptionPayment]:
    """Record an out-of-band payment and extend the tenant's paid period.

    The new period stacks on the remaining one (paying early never loses days):
    base = max(today, current_period_end). Optionally moves the tenant to a new
    plan in the same action (the usual "paid → pro" flow).
    """
    if method not in PAYMENT_METHODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown payment method: {method}")
    if plan_tier is not None and plan_tier not in PLAN_TIERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan tier: {plan_tier}")

    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    today = date.today()
    base = max(today, tenant.current_period_end or today)
    new_end = base + timedelta(days=PERIOD_DAYS_PER_MONTH * months)

    if plan_tier is not None:
        tenant.plan_tier = plan_tier
    tenant.subscription_status = "active"
    tenant.current_period_end = new_end

    payment = await SubscriptionPaymentRepository(db).add(
        tenant_id,
        SubscriptionPayment(
            amount_usd=amount_usd,
            method=method,
            months=months,
            note=note,
            plan_tier=tenant.plan_tier,
            period_end_after=new_end,
            recorded_by=admin.id,
        ),
    )
    await AuditService(db).record(
        tenant_id=tenant.id,
        actor_id=admin.id,
        action="tenant.payment_recorded",
        target=f"{amount_usd} USD via {method} ({months}mo → {new_end.isoformat()})",
    )
    await db.commit()
    log.info(
        "billing.payment_recorded",
        tenant_id=str(tenant.id),
        admin_id=str(admin.id),
        method=method,
        months=months,
        period_end=new_end.isoformat(),
    )
    return tenant, payment


async def apply_gateway_payment(
    db: AsyncSession,
    *,
    tenant: Tenant,
    months: int,
    amount_usd: Decimal,
    external_ref: int,
) -> Tenant:
    """Activate Pro after a SERVER-SIDE-verified Whish collect payment.

    Same period stacking as record_payment; recorded_by stays NULL (system
    action, not a founder). Does NOT commit — the caller commits atomically
    with the checkout row's status flip, so a crash can't activate without
    marking the checkout paid (or vice versa).
    """
    today = date.today()
    base = max(today, tenant.current_period_end or today)
    new_end = base + timedelta(days=PERIOD_DAYS_PER_MONTH * months)

    tenant.plan_tier = "pro"
    tenant.subscription_status = "active"
    tenant.current_period_end = new_end

    await SubscriptionPaymentRepository(db).add(
        tenant.id,
        SubscriptionPayment(
            amount_usd=amount_usd,
            method="whish",
            months=months,
            note=f"gateway external_id={external_ref}",
            plan_tier="pro",
            period_end_after=new_end,
            recorded_by=None,
        ),
    )
    await AuditService(db).record(
        tenant_id=tenant.id,
        actor_id=None,
        action="tenant.payment_recorded",
        target=f"{amount_usd} USD via whish gateway ({months}mo → {new_end.isoformat()})",
    )
    log.info(
        "billing.gateway_payment_applied",
        tenant_id=str(tenant.id),
        months=months,
        period_end=new_end.isoformat(),
    )
    return tenant


async def override_subscription(
    db: AsyncSession,
    *,
    admin: Admin,
    tenant_id: UUID,
    plan_tier: str,
    current_period_end: date | None,
) -> Tenant:
    """Founder override: set the subscription state directly.

    Covers everything record_payment doesn't: fixing a wrong entry, granting a
    custom period (e.g. a sponsored year), or RESETTING a subscription
    (plan_tier="free", current_period_end=None → back to a never-paid state).
    Level-3 (founder-initiated only), always audited with before → after.
    """
    if plan_tier not in PLAN_TIERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan tier: {plan_tier}")

    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    before = (
        f"{tenant.plan_tier}/"
        f"{tenant.current_period_end.isoformat() if tenant.current_period_end else '—'}"
    )
    tenant.plan_tier = plan_tier
    tenant.current_period_end = current_period_end
    tenant.subscription_status = "active" if current_period_end else "trialing"

    after = f"{plan_tier}/{current_period_end.isoformat() if current_period_end else '—'}"
    await AuditService(db).record(
        tenant_id=tenant.id,
        actor_id=admin.id,
        action="tenant.subscription_overridden",
        target=f"{before} → {after}",
    )
    await db.commit()
    log.info(
        "billing.subscription_overridden",
        tenant_id=str(tenant.id),
        admin_id=str(admin.id),
        plan_tier=plan_tier,
        period_end=current_period_end.isoformat() if current_period_end else None,
    )
    return tenant


async def set_plan(db: AsyncSession, *, admin: Admin, tenant_id: UUID, plan_tier: str) -> Tenant:
    """Change a tenant's plan tier without a payment (comp, downgrade, fix)."""
    if plan_tier not in PLAN_TIERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan tier: {plan_tier}")

    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    if tenant.plan_tier == plan_tier:
        raise HTTPException(status.HTTP_409_CONFLICT, "Tenant is already on this plan")

    previous = tenant.plan_tier
    tenant.plan_tier = plan_tier
    await AuditService(db).record(
        tenant_id=tenant.id,
        actor_id=admin.id,
        action="tenant.plan_changed",
        target=f"{previous} → {plan_tier}",
    )
    await db.commit()
    log.info(
        "billing.plan_changed",
        tenant_id=str(tenant.id),
        admin_id=str(admin.id),
        plan_tier=plan_tier,
    )
    return tenant
