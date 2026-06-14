from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.signup_request import (
    OtpRequest,
    OtpRequestResponse,
    SignupRequestCreate,
    SignupRequestPublicResponse,
)
from app.db.models import SignupRequest
from app.db.session import get_db_session
from app.infra.logging import get_logger
from app.infra.phone import normalize_lebanese_mobile
from app.infra.settings import Settings, get_settings
from app.repositories.signup_requests import SignupRequestRepository
from app.services.signup_otp import SignupOtpService
from prompts import signup_ar

router = APIRouter(prefix="/signup-requests", tags=["signup-requests"])
log = get_logger(__name__)


def _otp_service(request: Request, settings: Settings) -> SignupOtpService:
    """Build the OTP service from the lifespan singletons on app.state (the same
    Redis client the rate limiter uses, and the WhatsApp send client)."""
    return SignupOtpService(
        redis=request.app.state.redis,
        whatsapp=request.app.state.whatsapp_client,
        settings=settings,
    )


@router.post("/otp", response_model=OtpRequestResponse)
async def request_signup_otp(
    payload: OtpRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> OtpRequestResponse:
    """Public, step 1: WhatsApp a one-time code to the phone the applicant typed.

    Rejects a number that is not a valid Lebanese mobile (400) before sending, so
    we never message garbage. Abuse caps (cooldown + hourly limit) live in the
    service and surface as 429. The code is never returned in the response.
    """
    phone = normalize_lebanese_mobile(payload.owner_phone)
    if phone is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, signup_ar.INVALID_PHONE)

    await _otp_service(request, settings).request_code(phone)
    return OtpRequestResponse(sent_to=phone)


@router.post(
    "",
    response_model=SignupRequestPublicResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_signup_request(
    payload: SignupRequestCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SignupRequestPublicResponse:
    """Public, step 2: submit an application to join Modir.

    The phone must have been verified via /signup-requests/otp first — the
    `otp_code` is checked against the live code before anything is created. Only
    then does this create a PENDING request (NO tenant, NO user, NO login). A
    founder must approve it (Task 3.18) before any account exists. Deduped on a
    still-pending email so an applicant can't pile up duplicate pending rows; a
    previously rejected applicant may re-apply.
    """
    phone = normalize_lebanese_mobile(payload.owner_phone)
    if phone is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, signup_ar.INVALID_PHONE)

    # Prove the phone before touching the DB: a bad/expired code is rejected here.
    if not await _otp_service(request, settings).verify_code(phone, payload.otp_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, signup_ar.INVALID_OTP)

    repo = SignupRequestRepository(db)

    if await repo.get_pending_by_email(payload.owner_email):
        # 409: an application from this email is already awaiting review.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A pending request already exists for this email"
        )

    request_row = await repo.add(
        SignupRequest(
            business_name=payload.business_name,
            owner_phone=phone,
            owner_email=payload.owner_email,
            status="pending",
        )
    )
    await db.commit()

    # Above-tenant event (no tenant yet) → structured log, not the tenant-scoped
    # audit_log. The approve/reject steps are audited against the tenant later.
    log.info("signup_request.created", request_id=str(request_row.id))

    return SignupRequestPublicResponse.model_validate(request_row)
