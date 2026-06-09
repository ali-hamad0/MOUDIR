import time
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.webhook import WhatsAppWebhookPayload
from app.db.models import Admin, Tenant, User
from app.db.session import get_db_session
from app.domain.identity import ResolvedIdentity
from app.infra.rate_limiter import RateLimiter
from app.infra.security import decode_access_token
from app.infra.settings import Settings, get_settings
from app.repositories.admins import AdminRepository
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository
from app.services.identity_resolver import IdentityResolver

_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    try:
        payload = decode_access_token(settings, creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None

    # Reject anything that isn't a tenant-user access token — a founder "admin"
    # token must NEVER satisfy a tenant-user dependency (audience separation).
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")

    user_id = UUID(payload["sub"])
    tenant_id = UUID(payload["tenant_id"])

    # Load the user WITHIN the claimed tenant's scope. If the claim was tampered
    # with, the scoped lookup simply returns nothing → 401. The DB is the truth.
    user = await UserRepository(db).get(tenant_id, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found for tenant")
    return user


async def get_current_admin(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Admin:
    """Authorize the founder/super-admin for admin-only endpoints.

    Requires an "admin"-type token (a tenant-user "access" token is rejected).
    This dependency is the ONLY way an admin is authorized, and it is wired ONLY
    into dedicated admin routes — never into a tenant-scoped repository. The Wall
    holds: the founder cannot read tenant data through this path.
    """
    try:
        payload = decode_access_token(settings, creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from None

    if payload.get("type") != "admin":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")

    admin = await AdminRepository(db).get_by_id(UUID(payload["sub"]))
    if admin is None or not admin.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin not found or inactive")
    return admin


async def get_current_tenant(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Tenant:
    tenant = await TenantRepository(db).get_by_id(user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tenant inactive or missing")
    return tenant


async def resolve_message_identity(
    payload: WhatsAppWebhookPayload,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResolvedIdentity:
    """Phase 2's webhook route will depend on this to know who is talking and to
    which shop, before dispatching to an agent."""
    return await IdentityResolver(db).resolve(
        to=payload.to, from_=payload.from_, display_name=payload.display_name
    )


async def rate_limit_check(
    request: Request,
    identity: Annotated[ResolvedIdentity, Depends(resolve_message_identity)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    """Phase 8 — per-tenant rate limiting on inbound webhook messages.

    FastAPI deduplicates resolve_message_identity across this dependency and the
    calling route — the identity is resolved only once per request.  On limit
    exceeded raises HTTP 429 with a Lebanese Arabic message and Retry-After header.
    Fails open: a Redis outage allows traffic through (never blocks legitimate calls).
    """
    limiter: RateLimiter = request.app.state.rate_limiter
    limit = await limiter.get_limit(identity.tenant_id, db)
    result = await limiter.check_and_increment(identity.tenant_id, limit)

    if not result.allowed:
        reset_seconds = max(int(result.reset_at.timestamp() - time.time()), 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="عذراً، لقد تجاوزت الحد المسموح به. حاول بعد قليل.",
            headers={"Retry-After": str(reset_seconds)},
        )
