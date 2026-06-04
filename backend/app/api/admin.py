from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.admin import AdminLoginRequest, AdminTokenResponse
from app.db.session import get_db_session
from app.infra.logging import get_logger
from app.infra.security import create_admin_token, verify_password
from app.infra.settings import Settings, get_settings
from app.repositories.admins import AdminRepository

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger(__name__)


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
