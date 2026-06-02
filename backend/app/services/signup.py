import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import RegisterRequest
from app.db.models import BusinessProfile, Tenant, TenantOwner, User
from app.infra.security import hash_password
from app.repositories.business_profile import BusinessProfileRepository
from app.repositories.tenant_owners import TenantOwnerRepository
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository
from app.services.audit import AuditService
from prompts import auth_ar


@dataclass
class RegisteredTenant:
    """Result of a signup: the new tenant and the dashboard user to issue a
    token for."""

    tenant: Tenant
    user: User


async def register_tenant(db: AsyncSession, payload: RegisterRequest) -> RegisteredTenant:
    """Provision a whole shop in one transaction.

    Creates the Tenant, its first (pending) owner phone, a dashboard User, and a
    blank BusinessProfile, then records the signup in the audit log. Commits once.
    A duplicate whatsapp_number is a 409 — two tenants cannot share a destination.
    """
    tenants = TenantRepository(db)

    if await tenants.get_by_whatsapp_number(payload.whatsapp_number) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, auth_ar.WHATSAPP_ALREADY_REGISTERED)

    tenant = await tenants.add(
        Tenant(name=payload.business_name, whatsapp_number=payload.whatsapp_number)
    )

    user = await UserRepository(db).add(
        tenant.id,
        User(
            email=payload.email,
            hashed_password=hash_password(payload.password),
            role="owner",
        ),
    )

    # The first owner phone starts unverified — until verified (Task 1.11) the
    # identity resolver treats it as a customer, never granting owner tools.
    await TenantOwnerRepository(db).add(
        tenant.id,
        TenantOwner(
            phone_number=payload.owner_phone,
            verification_token=secrets.token_urlsafe(32),
            verification_status="pending",
        ),
    )

    # Blank profile so the shop has a profile row from day one (Task 1.12 fills it).
    await BusinessProfileRepository(db).add(tenant.id, BusinessProfile())

    await AuditService(db).record(tenant_id=tenant.id, actor_id=user.id, action="tenant.signup")

    await db.commit()
    return RegisteredTenant(tenant=tenant, user=user)
