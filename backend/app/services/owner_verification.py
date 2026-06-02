import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TenantOwner
from app.repositories.tenant_owners import TenantOwnerRepository
from app.services.audit import AuditService
from prompts import owners_ar


class OwnerVerificationService:
    """Add owner phones and verify them. Adding an owner phone grants owner-level
    agent access, so it is a privilege change: the phone starts `pending` and is
    trusted only after the verification token is confirmed. All operations are
    tenant-scoped through TenantOwnerRepository.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._owners = TenantOwnerRepository(session)
        self._audit = AuditService(session)

    async def add_owner(
        self, *, tenant_id: UUID, actor_id: UUID, phone_number: str, name: str | None
    ) -> TenantOwner:
        if await self._owners.get_by_phone(tenant_id, phone_number) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, owners_ar.OWNER_ALREADY_EXISTS)

        owner = await self._owners.add(
            tenant_id,
            TenantOwner(
                phone_number=phone_number,
                name=name,
                verification_token=secrets.token_urlsafe(32),
                verification_status="pending",
            ),
        )
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="owner.add_requested",
            target=phone_number,
        )
        await self._session.commit()
        return owner

    async def verify_owner(
        self, *, tenant_id: UUID, actor_id: UUID, owner_id: UUID, token: str
    ) -> TenantOwner:
        owner = await self._owners.get(tenant_id, owner_id)
        if owner is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, owners_ar.OWNER_NOT_FOUND)
        if owner.verification_status == "verified":
            raise HTTPException(status.HTTP_409_CONFLICT, owners_ar.OWNER_ALREADY_VERIFIED)

        # Constant-time compare so a wrong token leaks no timing signal. A bad
        # token leaves the owner pending — never trusted.
        if owner.verification_token is None or not secrets.compare_digest(
            owner.verification_token, token
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, owners_ar.INVALID_VERIFICATION_TOKEN)

        owner.verification_status = "verified"
        owner.verified_at = datetime.now(UTC)
        owner.verification_token = None  # single-use; clear once consumed
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="owner.verified",
            target=owner.phone_number,
        )
        await self._session.commit()
        return owner
