from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import LoginRequest, TokenResponse
from app.db.models import AuditLog
from app.db.session import get_db_session
from app.infra.security import create_access_token, verify_password
from app.infra.settings import Settings, get_settings
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository
from prompts import auth_ar

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Resolve the tenant by its WhatsApp number, then look the user up scoped
    to that tenant (email is unique per tenant, not globally). Verify the
    password and issue a short-lived access token.
    """
    # The same vague error for every failure mode below — never leak whether the
    # shop, the email, or the password was the wrong one.
    tenant = await TenantRepository(db).get_by_whatsapp_number(payload.whatsapp_number)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, auth_ar.INVALID_CREDENTIALS)

    user = await UserRepository(db).get_by_email(tenant.id, payload.email)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, auth_ar.INVALID_CREDENTIALS)

    # Audit the successful login. (Task 1.13 routes this through AuditService.)
    db.add(AuditLog(tenant_id=tenant.id, actor_id=user.id, action="user.login"))
    await db.commit()

    token = create_access_token(settings, user_id=user.id, tenant_id=tenant.id)
    return TokenResponse(access_token=token, expires_in_minutes=settings.jwt_expiry_minutes)
