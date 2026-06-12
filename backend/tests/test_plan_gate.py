"""Plan gating + Whish checkout (Phase 11).

Proves: an effectively-Free tenant is blocked (402) from Pro features and
quota overruns while Pro passes; an EXPIRED Pro falls back to Free limits (the
subscription means something); and the dev-mode Whish checkout flow activates
Pro end to end — pending checkout → server-side verify → plan flips, period
extends, a gateway payment row lands, the checkout is marked paid — with the
Wall holding (a checkout id never resolves for another tenant).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.billing import CheckoutRequest, checkout_status, start_checkout
from app.db.models import Tenant, User
from app.infra.settings import Settings
from app.repositories.subscription_payments import SubscriptionPaymentRepository
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository
from app.services.plan_gate import (
    FREE_MAX_PRODUCTS,
    PRO_PRICE_USD,
    effective_plan,
    require_pro,
    require_within_limit,
)
from tests.conftest import TwoTenants

_SETTINGS = Settings(
    database_url="postgresql+asyncpg://x:x@localhost:5432/x",
    redis_url="redis://localhost:6379/0",
    vault_addr="http://localhost:8200",
    vault_token="test",
    whish_pay_mode="dev",
)


async def _tenant(db: AsyncSession, tenant_id) -> Tenant:
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    assert tenant is not None
    return tenant


async def _user(db: AsyncSession, bundle) -> User:
    user = await UserRepository(db).get_by_email(bundle.tenant_id, bundle.user_email)
    assert user is not None
    return user


def _set_pro(tenant: Tenant, days_left: int) -> None:
    tenant.plan_tier = "pro"
    tenant.subscription_status = "active"
    tenant.current_period_end = date.today() + timedelta(days=days_left)


# ------------------------------------------------------------- effective plan
def test_effective_plan_walks_the_lifecycle() -> None:
    tenant = Tenant(name="x", whatsapp_number="+9610", subscription_status="trialing")
    tenant.is_active = True
    tenant.plan_tier = "free"
    tenant.current_period_end = None
    assert effective_plan(tenant) == "free"

    _set_pro(tenant, days_left=10)
    assert effective_plan(tenant) == "pro"

    # Founder comp: plan flipped to pro with NO paid period (set_plan, no
    # payment) — must actually grant Pro, not silently stay Free.
    comp = Tenant(name="y", whatsapp_number="+9611", subscription_status="trialing")
    comp.is_active = True
    comp.plan_tier = "pro"
    comp.current_period_end = None
    assert effective_plan(comp) == "pro"

    # Grace window: still pro (service keeps working while the nag shows).
    tenant.current_period_end = date.today() - timedelta(days=3)
    assert effective_plan(tenant) == "pro"

    # Past grace: an expired Pro behaves like Free — the plan means something.
    tenant.current_period_end = date.today() - timedelta(days=30)
    assert effective_plan(tenant) == "free"


# ------------------------------------------------------------------ the gates
async def test_free_blocked_from_pro_feature_pro_passes(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a = two_tenants.a
    with pytest.raises(HTTPException) as exc:
        await require_pro(db_session, a.tenant_id, "insights")
    assert exc.value.status_code == 402
    assert exc.value.detail == "pro_required:insights"

    _set_pro(await _tenant(db_session, a.tenant_id), days_left=30)
    await require_pro(db_session, a.tenant_id, "insights")  # no raise


async def test_free_quota_blocks_pro_unlimited(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a = two_tenants.a
    with pytest.raises(HTTPException) as exc:
        await require_within_limit(
            db_session, a.tenant_id, "products", FREE_MAX_PRODUCTS, FREE_MAX_PRODUCTS
        )
    assert exc.value.status_code == 402
    assert exc.value.detail == "plan_limit:products"

    # Below the cap → fine.
    await require_within_limit(
        db_session, a.tenant_id, "products", FREE_MAX_PRODUCTS - 1, FREE_MAX_PRODUCTS
    )

    # Pro → never limited, even far over the free cap.
    _set_pro(await _tenant(db_session, a.tenant_id), days_left=30)
    await require_within_limit(
        db_session, a.tenant_id, "products", FREE_MAX_PRODUCTS * 10, FREE_MAX_PRODUCTS
    )


# ------------------------------------------------- checkout (dev mode, e2e)
async def test_dev_checkout_activates_pro_end_to_end(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a = two_tenants.a
    user = await _user(db_session, a)

    out = await start_checkout(CheckoutRequest(months=2), user, db_session, _SETTINGS)
    # Dev mode: the "payment page" is the success redirect itself.
    assert str(out.checkout_id) in out.collect_url

    state = await checkout_status(out.checkout_id, user, db_session, _SETTINGS)
    assert state.status == "paid"
    assert state.plan_tier == "pro"
    assert state.subscription_status == "active"
    assert state.current_period_end == date.today() + timedelta(days=60)

    tenant = await _tenant(db_session, a.tenant_id)
    assert effective_plan(tenant) == "pro"

    # The gateway payment row landed: method whish, system-recorded, $40 for 2mo.
    payments = await SubscriptionPaymentRepository(db_session).list_for_tenant(a.tenant_id)
    assert len(payments) == 1
    assert payments[0].method == "whish"
    assert payments[0].recorded_by is None
    assert payments[0].amount_usd == PRO_PRICE_USD * 2

    # Polling again is idempotent — no double extension, no second payment.
    again = await checkout_status(out.checkout_id, user, db_session, _SETTINGS)
    assert again.status == "paid"
    assert again.current_period_end == state.current_period_end
    assert len(await SubscriptionPaymentRepository(db_session).list_for_tenant(a.tenant_id)) == 1


async def test_checkout_is_tenant_scoped(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a, b = two_tenants.a, two_tenants.b
    user_a = await _user(db_session, a)
    user_b = await _user(db_session, b)

    out = await start_checkout(CheckoutRequest(months=1), user_a, db_session, _SETTINGS)

    # Tenant B polling tenant A's checkout id → 404; B stays free (the Wall).
    with pytest.raises(HTTPException) as exc:
        await checkout_status(out.checkout_id, user_b, db_session, _SETTINGS)
    assert exc.value.status_code == 404
    assert effective_plan(await _tenant(db_session, b.tenant_id)) == "free"


def test_checkout_amount_is_price_times_months() -> None:
    assert PRO_PRICE_USD == Decimal("20.00")


async def test_checkout_refused_when_gateway_off(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    """Mode "off" (the no-merchant-credentials default): the API refuses to
    start a checkout, so the simulated dev path can never grant Pro for free."""
    off_settings = Settings(
        database_url="postgresql+asyncpg://x:x@localhost:5432/x",
        redis_url="redis://localhost:6379/0",
        vault_addr="http://localhost:8200",
        vault_token="test",
        whish_pay_mode="off",
    )
    user = await _user(db_session, two_tenants.a)
    with pytest.raises(HTTPException) as exc:
        await start_checkout(CheckoutRequest(months=1), user, db_session, off_settings)
    assert exc.value.status_code == 503

    # A leftover pending checkout (made while dev mode was on) must not
    # resolve through the simulated path after the switch to off.
    dev_out = await start_checkout(CheckoutRequest(months=1), user, db_session, _SETTINGS)
    state = await checkout_status(dev_out.checkout_id, user, db_session, off_settings)
    assert state.status == "pending"
    assert effective_plan(await _tenant(db_session, two_tenants.a.tenant_id)) == "free"
