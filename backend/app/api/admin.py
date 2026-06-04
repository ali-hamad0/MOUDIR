from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.api.schemas.admin import AdminLoginRequest, AdminTokenResponse
from app.api.schemas.admin_requests import (
    AdminActionResponse,
    ApproveRequest,
    RejectRequest,
    SignupRequestAdminView,
)
from app.db.models import Admin
from app.db.session import get_db_session
from app.infra.email import EmailSender
from app.infra.logging import get_logger
from app.infra.security import create_admin_token, verify_password
from app.infra.settings import Settings, get_settings
from app.repositories.admins import AdminRepository
from app.repositories.signup_requests import SignupRequestRepository
from app.services.onboarding import approve_request, reject_request

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger(__name__)

# Founder-only dependencies for the admin surface below.
CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]
Db = Annotated[AsyncSession, Depends(get_db_session)]
Cfg = Annotated[Settings, Depends(get_settings)]


@router.post("/login", response_model=AdminTokenResponse)
async def admin_login(
    payload: AdminLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminTokenResponse:
    """Founder/super-admin login. Issues an "admin"-type token that a tenant-user
    dependency rejects. Same vague error for every failure mode — never leak
    whether the email or the password was wrong.
    """
    admin = await AdminRepository(db).get_by_email(payload.email)
    if (
        admin is None
        or not admin.is_active
        or not verify_password(payload.password, admin.hashed_password)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    # Above-tenant event: there is no tenant_id to attach to audit_log (which is
    # tenant-scoped), so we record it in the structured log. Tenant-scoped admin
    # actions (approve/reject) are audited against the provisioned tenant later.
    log.info("admin.login", admin_id=str(admin.id))

    token = create_admin_token(settings, admin_id=admin.id)
    return AdminTokenResponse(access_token=token, expires_in_minutes=settings.jwt_expiry_minutes)


@router.get("/signup-requests", response_model=list[SignupRequestAdminView])
async def list_signup_requests(
    _admin: CurrentAdmin,
    db: Db,
    status_filter: str | None = None,
) -> list[SignupRequestAdminView]:
    """Founder-only: list signup requests, optionally filtered by status."""
    rows = await SignupRequestRepository(db).list_by_status(status_filter)
    return [SignupRequestAdminView.model_validate(r) for r in rows]


@router.post("/signup-requests/{request_id}/approve", response_model=AdminActionResponse)
async def approve_signup_request(
    request_id: UUID,
    payload: ApproveRequest,
    admin: CurrentAdmin,
    db: Db,
    settings: Cfg,
) -> AdminActionResponse:
    """Founder-only: approve a request → provision the tenant (via register_tenant),
    issue a one-time activation token, and email the link."""
    req = await approve_request(
        db,
        settings=settings,
        mailer=EmailSender(settings),
        admin=admin,
        request_id=request_id,
        whatsapp_number=payload.whatsapp_number,
    )
    return AdminActionResponse(
        id=req.id, status=req.status, provisioned_tenant_id=req.provisioned_tenant_id
    )


@router.post("/signup-requests/{request_id}/reject", response_model=AdminActionResponse)
async def reject_signup_request(
    request_id: UUID,
    payload: RejectRequest,
    admin: CurrentAdmin,
    db: Db,
) -> AdminActionResponse:
    """Founder-only: reject a request with a reason. Provisions nothing."""
    req = await reject_request(db, admin=admin, request_id=request_id, reason=payload.reason)
    return AdminActionResponse(id=req.id, status=req.status)
