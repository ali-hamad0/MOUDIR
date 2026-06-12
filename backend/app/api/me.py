from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.me import MeResponse
from app.db.models import User
from app.db.session import get_db_session
from app.infra.settings import Settings, get_settings
from app.repositories.business_profile import BusinessProfileRepository
from app.repositories.products import ProductRepository
from app.repositories.tenants import TenantRepository
from app.services.billing import effective_subscription_status
from app.services.plan_gate import PRO_PRICE_USD, effective_plan

router = APIRouter(tags=["me"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]
Cfg = Annotated[Settings, Depends(get_settings)]


@router.get("/me", response_model=MeResponse)
async def whoami(user: CurrentUser, db: Db, settings: Cfg) -> MeResponse:
    """The logged-in user's tenant identity plus a derived setup_complete flag.

    setup_complete = the profile has a business_name AND the catalog has at least
    one product — so the dashboard launches the wizard on first login and shows
    the "setup incomplete" banner otherwise.
    """
    tenant_id = user.tenant_id

    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:  # the JWT resolved a user whose tenant vanished — treat as auth failure
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tenant not found")

    profile = await BusinessProfileRepository(db).get_for_tenant(tenant_id)
    product_count = await ProductRepository(db).count(tenant_id)

    business_name = profile.business_name if profile else None
    setup_complete = bool(business_name) and product_count > 0

    return MeResponse(
        user_id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=tenant_id,
        business_name=business_name,
        whatsapp_number=tenant.whatsapp_number,
        plan_tier=tenant.plan_tier,
        product_count=product_count,
        setup_complete=setup_complete,
        subscription_status=effective_subscription_status(tenant),
        current_period_end=tenant.current_period_end,
        billing_whish_link=settings.billing_whish_link,
        billing_contact_phone=settings.billing_contact_phone,
        effective_plan=effective_plan(tenant),
        pro_price_usd=float(PRO_PRICE_USD),
        online_checkout_enabled=settings.whish_pay_mode != "off",
    )
