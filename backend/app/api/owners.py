from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.owners import AddOwnerRequest, OwnerResponse, VerifyOwnerRequest
from app.db.models import User
from app.db.session import get_db_session
from app.services.owner_verification import OwnerVerificationService

router = APIRouter(prefix="/owners", tags=["owners"])


@router.post("", response_model=OwnerResponse, status_code=status.HTTP_201_CREATED)
async def add_owner(
    payload: AddOwnerRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> OwnerResponse:
    """Add an owner phone (pending). The verification token is delivered to the
    phone out-of-band (WhatsApp) — it is deliberately NOT returned here."""
    owner = await OwnerVerificationService(db).add_owner(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        phone_number=payload.phone_number,
        name=payload.name,
    )
    return OwnerResponse.model_validate(owner)


@router.post("/{owner_id}/verify", response_model=OwnerResponse)
async def verify_owner(
    owner_id: UUID,
    payload: VerifyOwnerRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> OwnerResponse:
    """Confirm a pending owner phone with its token. On success the phone becomes
    a trusted owner; the identity resolver will then route it as `owner`."""
    owner = await OwnerVerificationService(db).verify_owner(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        owner_id=owner_id,
        token=payload.token,
    )
    return OwnerResponse.model_validate(owner)
