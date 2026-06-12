"""Owner billing API (Phase 11): online Pro checkout through Whish Pay.

POST /billing/checkout starts a collect: a pending billing_checkouts row plus
the Whish-hosted payment URL the dashboard redirects to. GET
/billing/checkout/{id} is what the result page polls — it verifies the payment
with Whish SERVER-SIDE (channel+secret) and only then activates Pro, so a
forged redirect can never grant a subscription. The Wall: the checkout is
looked up tenant-scoped through the JWT; a checkout can only activate its own
tenant.
"""

import secrets as pysecrets
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import BillingCheckout, User
from app.db.session import get_db_session
from app.infra.logging import get_logger
from app.infra.settings import Settings, get_settings
from app.infra.whish_pay import FAILED, SUCCESS, WhishPayClient, WhishPayError
from app.repositories.billing_checkouts import BillingCheckoutRepository
from app.repositories.tenants import TenantRepository
from app.services.billing import apply_gateway_payment, effective_subscription_status
from app.services.plan_gate import PRO_PRICE_USD

router = APIRouter(prefix="/billing", tags=["billing"])
log = get_logger(__name__)

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]
Cfg = Annotated[Settings, Depends(get_settings)]


class CheckoutRequest(BaseModel):
    months: int = Field(default=1, ge=1, le=12)


class CheckoutOut(BaseModel):
    checkout_id: UUID
    collect_url: str


class CheckoutStatusOut(BaseModel):
    status: str  # "pending" | "paid" | "failed"
    plan_tier: str
    subscription_status: str
    current_period_end: date | None = None


@router.post("/checkout", response_model=CheckoutOut)
async def start_checkout(
    body: CheckoutRequest, user: CurrentUser, db: Db, settings: Cfg
) -> CheckoutOut:
    """Start a Pro checkout; returns the Whish payment page to redirect to."""
    # Mode "off" = no gateway configured. Refusing HERE (not just hiding the
    # button) means a hand-crafted API call can never reach the simulated
    # dev-mode success and grant Pro for free.
    if settings.whish_pay_mode == "off":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "checkout_unavailable")
    amount = PRO_PRICE_USD * body.months
    # Whish tracks the collect by a numeric externalId; 48 random bits keeps it
    # unguessable and inside a Postgres BigInteger.
    external_id = pysecrets.randbits(48)

    row = await BillingCheckoutRepository(db).add(
        user.tenant_id,
        BillingCheckout(months=body.months, amount_usd=amount, external_id=external_id),
    )
    result_base = settings.billing_result_base_url
    try:
        collect_url = await WhishPayClient(settings).create_collect(
            amount_usd=amount,
            external_id=external_id,
            invoice=f"اشتراك مودير برو — {body.months} شهر",
            success_redirect=f"{result_base}?checkout={row.id}&outcome=success",
            failure_redirect=f"{result_base}?checkout={row.id}&outcome=failure",
        )
    except WhishPayError as err:
        await db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "payment_gateway_unavailable") from err

    row.collect_url = collect_url
    await db.commit()
    log.info(
        "billing.checkout_started",
        tenant_id=str(user.tenant_id),
        checkout_id=str(row.id),
        months=body.months,
    )
    return CheckoutOut(checkout_id=row.id, collect_url=collect_url)


@router.get("/checkout/{checkout_id}", response_model=CheckoutStatusOut)
async def checkout_status(
    checkout_id: UUID, user: CurrentUser, db: Db, settings: Cfg
) -> CheckoutStatusOut:
    """Poll a checkout. Verifies with Whish server-side; activates Pro on success."""
    row = await BillingCheckoutRepository(db).get(user.tenant_id, checkout_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkout not found")
    tenant = await TenantRepository(db).get_by_id(user.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tenant not found")

    # Mode "off": never verify/activate — a leftover pending checkout from a
    # previous mode must not resolve through the simulated dev path.
    if row.status == "pending" and settings.whish_pay_mode != "off":
        try:
            gateway_status = await WhishPayClient(settings).get_status(row.external_id)
        except WhishPayError:
            gateway_status = "pending"  # transient gateway trouble → keep polling
        if gateway_status == SUCCESS:
            tenant = await apply_gateway_payment(
                db,
                tenant=tenant,
                months=row.months,
                amount_usd=row.amount_usd,
                external_ref=row.external_id,
            )
            row.status = "paid"
            await db.commit()
            log.info(
                "billing.checkout_paid",
                tenant_id=str(user.tenant_id),
                checkout_id=str(row.id),
            )
        elif gateway_status == FAILED:
            row.status = "failed"
            await db.commit()
            log.info(
                "billing.checkout_failed",
                tenant_id=str(user.tenant_id),
                checkout_id=str(row.id),
            )

    return CheckoutStatusOut(
        status=row.status,
        plan_tier=tenant.plan_tier,
        subscription_status=effective_subscription_status(tenant),
        current_period_end=tenant.current_period_end,
    )
