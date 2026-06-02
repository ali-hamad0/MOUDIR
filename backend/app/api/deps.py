from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.webhook import WhatsAppWebhookPayload
from app.db.models import Tenant, User
from app.db.session import get_db_session
from app.domain.identity import ResolvedIdentity
from app.infra.security import decode_access_token
from app.infra.settings import Settings, get_settings
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

    user_id = UUID(payload["sub"])
    tenant_id = UUID(payload["tenant_id"])

    # Load the user WITHIN the claimed tenant's scope. If the claim was tampered
    # with, the scoped lookup simply returns nothing → 401. The DB is the truth.
    user = await UserRepository(db).get(tenant_id, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found for tenant")
    return user


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
