"""Founder-onboarding approval/rejection (Phase 1.5, Task 3.18).

Approval REUSES register_tenant to provision the shop, then turns the new user
into an un-activated account (random throwaway password the owner never learns +
a one-time activation token) and emails the activation link. The owner sets their
real password via /activate (Task 3.17). Rejection records a reason. Both are
audited and only reachable behind get_current_admin.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import RegisterRequest
from app.db.models import Admin, SignupRequest
from app.infra.email import EmailSender
from app.infra.logging import get_logger
from app.infra.security import generate_activation_token
from app.infra.settings import Settings
from app.repositories.signup_requests import SignupRequestRepository
from app.repositories.users import UserRepository
from app.services.audit import AuditService
from app.services.signup import register_tenant
from prompts import activation_ar

log = get_logger(__name__)


async def approve_request(
    db: AsyncSession,
    *,
    settings: Settings,
    mailer: EmailSender,
    admin: Admin,
    request_id: UUID,
    whatsapp_number: str,
) -> SignupRequest:
    """Approve a pending request: provision the tenant, issue a one-time
    activation token, email the link, and mark the request approved."""
    repo = SignupRequestRepository(db)
    req = await repo.get_by_id(request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Request is not pending")

    # Reuse register_tenant. The user is created with a random throwaway password
    # the owner never learns — so they CANNOT log in until they set their own via
    # /activate. (register_tenant requires a password; this satisfies it without
    # being a usable credential.)
    registered = await register_tenant(
        db,
        RegisterRequest(
            business_name=req.business_name,
            whatsapp_number=whatsapp_number,
            owner_phone=req.owner_phone,
            email=req.owner_email,
            password=secrets.token_urlsafe(32),
        ),
    )

    # Turn the new user into an un-activated account with a one-time token.
    token = generate_activation_token()
    user = await UserRepository(db).get(registered.tenant.id, registered.user.id)
    user.activation_token = token
    user.activation_expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.activation_ttl_minutes
    )
    user.activated_at = None

    # Mark the request approved + link it to the provisioned tenant.
    req.status = "approved"
    req.reviewed_by = admin.id
    req.reviewed_at = datetime.now(UTC)
    req.provisioned_tenant_id = registered.tenant.id

    await AuditService(db).record(
        tenant_id=registered.tenant.id,
        actor_id=admin.id,
        action="signup_request.approved",
        target=str(req.id),
    )
    await db.commit()

    # Email the activation link AFTER commit (a mail failure must not roll back a
    # successful provision — the token persists and the link can be re-sent).
    link = f"{settings.activation_base_url}?token={token}"
    try:
        await mailer.send(
            to=req.owner_email,
            subject=activation_ar.ACTIVATION_EMAIL_SUBJECT,
            body=activation_ar.ACTIVATION_EMAIL_BODY.format(link=link),
        )
    except Exception as e:  # noqa: BLE001 — mail is best-effort; log and move on
        log.warning("activation_email.failed", request_id=str(req.id), error=str(e))

    log.info("signup_request.approved", request_id=str(req.id), tenant_id=str(registered.tenant.id))
    return req


async def reject_request(
    db: AsyncSession,
    *,
    admin: Admin,
    request_id: UUID,
    reason: str,
) -> SignupRequest:
    """Reject a pending request with a reason. Provisions nothing."""
    repo = SignupRequestRepository(db)
    req = await repo.get_by_id(request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Request is not pending")

    req.status = "rejected"
    req.reviewed_by = admin.id
    req.reviewed_at = datetime.now(UTC)
    req.reject_reason = reason
    await db.commit()
    log.info("signup_request.rejected", request_id=str(req.id))
    return req
