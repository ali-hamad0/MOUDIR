from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.activation import (
    ActivateRequest,
    ActivateResponse,
    ActivationCheckResponse,
)
from app.db.models import User
from app.db.session import get_db_session
from app.infra.logging import get_logger
from app.infra.security import hash_password
from app.repositories.users import UserRepository
from app.services.audit import AuditService
from prompts import activation_ar

router = APIRouter(tags=["activation"])
log = get_logger(__name__)


def _is_usable(user: User | None) -> bool:
    """A token is usable iff it belongs to a not-yet-activated user and hasn't
    expired. Single-use is enforced by clearing the token on activation, so an
    already-activated user (token cleared) simply isn't found."""
    if user is None or user.activated_at is not None:
        return False
    if user.activation_expires_at is None:
        return False
    return user.activation_expires_at > datetime.now(UTC)


@router.get("/activate", response_model=ActivationCheckResponse)
async def check_activation(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ActivationCheckResponse:
    """Validate a one-time token for the set-password screen. Returns the owner's
    email when valid; reveals nothing when not."""
    user = await UserRepository(db).get_by_activation_token(token)
    if not _is_usable(user):
        return ActivationCheckResponse(valid=False)
    return ActivationCheckResponse(valid=True, email=user.email)


@router.post("/activate", response_model=ActivateResponse)
async def activate(
    payload: ActivateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ActivateResponse:
    """The owner sets their own password via the one-time link. Sets the hashed
    password, stamps activated_at, and BURNS the token (single-use). A used,
    expired, or invalid token is rejected — no plaintext password is ever emailed.
    """
    repo = UserRepository(db)
    user = await repo.get_by_activation_token(payload.token)
    if not _is_usable(user):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, activation_ar.ACTIVATION_INVALID)

    user.hashed_password = hash_password(payload.new_password)
    user.activated_at = datetime.now(UTC)
    user.activation_token = None  # single-use: burn it
    user.activation_expires_at = None

    # Now there is a tenant to attach the audit row to (the owner's tenant).
    await AuditService(db).record(
        tenant_id=user.tenant_id, actor_id=user.id, action="user.activated"
    )
    await db.commit()
    log.info("user.activated", user_id=str(user.id))

    return ActivateResponse(message=activation_ar.ACTIVATION_DONE)
