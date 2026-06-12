"""Manual subscription billing (Phase 11).

Proves, at the service/route layer (the project's harness convention): a
recorded payment activates the subscription and extends the paid period;
periods STACK (paying early never loses days); a payment can move the plan in
the same action; payment history is tenant-scoped (The Wall holds on the
billing surface); plan changes are validated, idempotency-guarded and audited;
and the derived status walks trialing → active → past_due → expired correctly.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Admin, AuditLog, Tenant
from app.repositories.admins import AdminRepository
from app.repositories.subscription_payments import SubscriptionPaymentRepository
from app.repositories.tenants import TenantRepository
from app.services.billing import (
    GRACE_DAYS,
    effective_subscription_status,
    override_subscription,
    record_payment,
    set_plan,
)
from tests.conftest import TwoTenants


async def _admin(db: AsyncSession) -> Admin:
    from app.infra.security import hash_password

    admin = Admin(email="founder@test.com", hashed_password=hash_password("x"), is_active=True)
    return await AdminRepository(db).add(admin)


# ------------------------------------------------------------- record payment
async def test_first_payment_activates_and_extends(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    tenant, payment = await record_payment(
        db_session,
        admin=admin,
        tenant_id=two_tenants.a.tenant_id,
        amount_usd=Decimal("20.00"),
        method="whish",
    )

    assert tenant.subscription_status == "active"
    assert tenant.current_period_end == date.today() + timedelta(days=30)
    assert payment.tenant_id == two_tenants.a.tenant_id
    assert payment.period_end_after == tenant.current_period_end
    assert payment.plan_tier == tenant.plan_tier

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == two_tenants.a.tenant_id,
                AuditLog.action == "tenant.payment_recorded",
            )
        )
    ).scalar_one()
    assert audit.actor_id == admin.id
    assert "whish" in audit.target


async def test_second_payment_stacks_on_remaining_period(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    tenant = await TenantRepository(db_session).get_by_id(two_tenants.a.tenant_id)
    assert tenant is not None
    tenant.current_period_end = date.today() + timedelta(days=10)
    tenant.subscription_status = "active"

    updated, _ = await record_payment(
        db_session,
        admin=admin,
        tenant_id=two_tenants.a.tenant_id,
        amount_usd=Decimal("20.00"),
        method="cash",
    )
    # 10 remaining days + 30 paid days; paying early never loses days.
    assert updated.current_period_end == date.today() + timedelta(days=40)


async def test_payment_can_move_plan_in_same_action(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    tenant, payment = await record_payment(
        db_session,
        admin=admin,
        tenant_id=two_tenants.a.tenant_id,
        amount_usd=Decimal("25.00"),
        method="omt",
        months=3,
        plan_tier="pro",
    )
    assert tenant.plan_tier == "pro"
    assert payment.plan_tier == "pro"
    assert tenant.current_period_end == date.today() + timedelta(days=90)


async def test_unknown_method_and_plan_rejected(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    with pytest.raises(HTTPException) as exc:
        await record_payment(
            db_session,
            admin=admin,
            tenant_id=two_tenants.a.tenant_id,
            amount_usd=Decimal("20.00"),
            method="paypal",
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await record_payment(
            db_session,
            admin=admin,
            tenant_id=two_tenants.a.tenant_id,
            amount_usd=Decimal("20.00"),
            method="whish",
            plan_tier="platinum",
        )
    assert exc.value.status_code == 400


# ------------------------------------------------------------------- the Wall
async def test_payment_history_is_tenant_scoped(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    await record_payment(
        db_session,
        admin=admin,
        tenant_id=two_tenants.a.tenant_id,
        amount_usd=Decimal("20.00"),
        method="whish",
    )

    repo = SubscriptionPaymentRepository(db_session)
    assert len(await repo.list_for_tenant(two_tenants.a.tenant_id)) == 1
    assert len(await repo.list_for_tenant(two_tenants.b.tenant_id)) == 0


# ------------------------------------------------------------------- set plan
async def test_set_plan_changes_and_audits(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    tenant = await set_plan(
        db_session, admin=admin, tenant_id=two_tenants.a.tenant_id, plan_tier="pro"
    )
    assert tenant.plan_tier == "pro"

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == two_tenants.a.tenant_id,
                AuditLog.action == "tenant.plan_changed",
            )
        )
    ).scalar_one()
    assert audit.target == "free → pro"

    # Same plan again → 409 (double-click / stale screen guard).
    with pytest.raises(HTTPException) as exc:
        await set_plan(db_session, admin=admin, tenant_id=two_tenants.a.tenant_id, plan_tier="pro")
    assert exc.value.status_code == 409

    # Unknown tier → 400.
    with pytest.raises(HTTPException) as exc:
        await set_plan(
            db_session, admin=admin, tenant_id=two_tenants.a.tenant_id, plan_tier="platinum"
        )
    assert exc.value.status_code == 400


# ------------------------------------------------------- founder override/reset
async def test_override_grants_custom_period_and_reset_clears(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    a = two_tenants.a
    custom_end = date.today() + timedelta(days=365)

    # Grant: pro for a custom year.
    tenant = await override_subscription(
        db_session,
        admin=admin,
        tenant_id=a.tenant_id,
        plan_tier="pro",
        current_period_end=custom_end,
    )
    assert tenant.plan_tier == "pro"
    assert tenant.subscription_status == "active"
    assert tenant.current_period_end == custom_end

    # Reset: back to a never-paid state.
    tenant = await override_subscription(
        db_session,
        admin=admin,
        tenant_id=a.tenant_id,
        plan_tier="free",
        current_period_end=None,
    )
    assert tenant.plan_tier == "free"
    assert tenant.subscription_status == "trialing"
    assert tenant.current_period_end is None

    # Both actions audited with before → after.
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == a.tenant_id,
                    AuditLog.action == "tenant.subscription_overridden",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 2
    assert audits[0].target == f"free/— → pro/{custom_end.isoformat()}"
    assert audits[1].target == f"pro/{custom_end.isoformat()} → free/—"

    # Unknown tier → 400.
    with pytest.raises(HTTPException) as exc:
        await override_subscription(
            db_session,
            admin=admin,
            tenant_id=a.tenant_id,
            plan_tier="platinum",
            current_period_end=None,
        )
    assert exc.value.status_code == 400


# ------------------------------------------------------------- derived status
def test_effective_status_walks_the_lifecycle() -> None:
    today = date(2026, 6, 12)
    tenant = Tenant(name="x", whatsapp_number="+9610", subscription_status="trialing")
    tenant.is_active = True
    tenant.current_period_end = None
    assert effective_subscription_status(tenant, today) == "trialing"

    tenant.current_period_end = today  # paid through today inclusive
    assert effective_subscription_status(tenant, today) == "active"

    tenant.current_period_end = today - timedelta(days=GRACE_DAYS)
    assert effective_subscription_status(tenant, today) == "past_due"

    tenant.current_period_end = today - timedelta(days=GRACE_DAYS + 1)
    assert effective_subscription_status(tenant, today) == "expired"

    tenant.is_active = False  # founder suspension overrides everything
    assert effective_subscription_status(tenant, today) == "suspended"
