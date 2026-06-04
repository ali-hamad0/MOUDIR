from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.signup_request import (
    SignupRequestCreate,
    SignupRequestPublicResponse,
)
from app.db.models import SignupRequest
from app.db.session import get_db_session
from app.infra.logging import get_logger
from app.repositories.signup_requests import SignupRequestRepository

router = APIRouter(prefix="/signup-requests", tags=["signup-requests"])
log = get_logger(__name__)


@router.post(
    "",
    response_model=SignupRequestPublicResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_signup_request(
    payload: SignupRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SignupRequestPublicResponse:
    """Public: submit an application to join Modir.

    Creates a PENDING request only — NO tenant, NO user, NO login. A founder must
    approve it (Task 3.18) before any account exists. Deduped on a still-pending
    email so an applicant can't pile up duplicate pending rows; a previously
    rejected applicant may re-apply.
    """
    repo = SignupRequestRepository(db)

    if await repo.get_pending_by_email(payload.owner_email):
        # 409: an application from this email is already awaiting review.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A pending request already exists for this email"
        )

    request = await repo.add(
        SignupRequest(
            business_name=payload.business_name,
            owner_phone=payload.owner_phone,
            owner_email=payload.owner_email,
            status="pending",
        )
    )
    await db.commit()

    # Above-tenant event (no tenant yet) → structured log, not the tenant-scoped
    # audit_log. The approve/reject steps are audited against the tenant later.
    log.info("signup_request.created", request_id=str(request.id))

    return SignupRequestPublicResponse.model_validate(request)
