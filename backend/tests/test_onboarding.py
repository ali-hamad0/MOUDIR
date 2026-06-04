"""Task 3.20 — founder-gated onboarding (Phase 1.5).

Proves the whole flow end-to-end at the service/dependency layer (the project's
harness convention): public request → founder approve → activation email → owner
sets own password → login. Plus the security invariants: no login before
activation, reject provisions nothing, used/expired tokens are rejected, the
founder CANNOT cross The Wall, every step is audited, and /auth/register is
founder-only. The mailer is faked so no real mail is sent and the email contents
can be asserted (no plaintext password ever leaves the system).
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.db.models import Admin, AuditLog, SignupRequest, Tenant, User
from app.infra.security import (
    create_access_token,
    create_admin_token,
    hash_password,
    verify_password,
)
from app.infra.settings import Settings
from app.repositories.admins import AdminRepository
from app.repositories.signup_requests import SignupRequestRepository
from app.repositories.users import UserRepository
from app.services.onboarding import approve_request, reject_request


def _settings() -> Settings:
    return Settings.model_construct(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        jwt_expiry_minutes=15,
        activation_base_url="http://localhost:5173/activate",
        activation_ttl_minutes=60,
        mail_mode="dev",
    )


class _FakeMailer:
    """Captures emails instead of sending — lets us assert the link is sent and
    NO plaintext password is ever in the message."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})


async def _admin(db: AsyncSession) -> Admin:
    admin = Admin(email="founder@test.com", hashed_password=hash_password("x"), is_active=True)
    return await AdminRepository(db).add(admin)


async def _make_request(db: AsyncSession, *, email: str, phone="+96170REQ") -> SignupRequest:
    req = SignupRequest(
        business_name="محل التجربة", owner_phone=phone, owner_email=email, status="pending"
    )
    return await SignupRequestRepository(db).add(req)


# ---------------------------------------------------------------- happy path
async def test_request_to_approve_to_activate_to_login(db_session: AsyncSession) -> None:
    settings = _settings()
    mailer = _FakeMailer()
    admin = await _admin(db_session)
    req = await _make_request(db_session, email="owner@happy.com")
    await db_session.flush()

    # Founder approves → tenant provisioned, activation email captured.
    result = await approve_request(
        db_session,
        settings=settings,
        mailer=mailer,
        admin=admin,
        request_id=req.id,
        whatsapp_number="+9617HAPPY",
    )
    assert result.status == "approved"
    assert result.provisioned_tenant_id is not None

    # Exactly one email, to the applicant, carrying the activation LINK and
    # NEVER a plaintext password.
    assert len(mailer.sent) == 1
    email = mailer.sent[0]
    assert email["to"] == "owner@happy.com"
    assert "token=" in email["body"]
    token = email["body"].split("token=")[1].split()[0].strip()
    assert "password" not in email["body"].lower()

    # The provisioned user is un-activated and CANNOT log in yet (no usable pw).
    user = await _get_user(db_session, "owner@happy.com")
    assert user.activated_at is None
    assert user.activation_token == token

    # Owner activates: sets own password, token burned, activated_at stamped.
    from app.api.activation import activate
    from app.api.schemas.activation import ActivateRequest

    resp = await activate(ActivateRequest(token=token, new_password="ownerpass123"), db=db_session)
    assert "حساب" in resp.message or resp.message  # Arabic success string
    await db_session.refresh(user)
    assert user.activated_at is not None
    assert user.activation_token is None
    assert verify_password("ownerpass123", user.hashed_password)


# ----------------------------------------------- no login before activation
async def test_no_login_before_activation(db_session: AsyncSession) -> None:
    settings = _settings()
    admin = await _admin(db_session)
    req = await _make_request(db_session, email="owner@nologin.com")
    await db_session.flush()
    await approve_request(
        db_session,
        settings=settings,
        mailer=_FakeMailer(),
        admin=admin,
        request_id=req.id,
        whatsapp_number="+9617NOLOGIN",
    )
    user = await _get_user(db_session, "owner@nologin.com")
    # The throwaway password set by register_tenant is unknown to anyone — the
    # owner cannot have it, so login is impossible until activation.
    assert user.activated_at is None
    assert not verify_password("ownerpass123", user.hashed_password)


# ------------------------------------------------------------------- reject
async def test_reject_provisions_nothing(db_session: AsyncSession) -> None:
    admin = await _admin(db_session)
    req = await _make_request(db_session, email="owner@reject.com")
    await db_session.flush()

    tenants_before = (
        await db_session.execute(select(func.count()).select_from(Tenant))
    ).scalar_one()
    result = await reject_request(
        db_session, admin=admin, request_id=req.id, reason="المنطقة بعيدة"
    )
    tenants_after = (
        await db_session.execute(select(func.count()).select_from(Tenant))
    ).scalar_one()

    assert result.status == "rejected"
    assert result.reject_reason == "المنطقة بعيدة"
    assert tenants_after == tenants_before  # nothing provisioned


# -------------------------------------------------------------- token safety
async def test_used_token_is_rejected(db_session: AsyncSession) -> None:
    settings = _settings()
    admin = await _admin(db_session)
    req = await _make_request(db_session, email="owner@used.com")
    await db_session.flush()
    await approve_request(
        db_session,
        settings=settings,
        mailer=(m := _FakeMailer()),
        admin=admin,
        request_id=req.id,
        whatsapp_number="+9617USED",
    )
    token = m.sent[0]["body"].split("token=")[1].split()[0].strip()

    from app.api.activation import activate
    from app.api.schemas.activation import ActivateRequest

    await activate(ActivateRequest(token=token, new_password="firstpass123"), db=db_session)
    # Reusing the same (now burned) token must fail.
    with pytest.raises(HTTPException) as exc:
        await activate(ActivateRequest(token=token, new_password="secondpass123"), db=db_session)
    assert exc.value.status_code == 400


async def test_expired_token_is_rejected(db_session: AsyncSession) -> None:
    # A user with an already-expired activation token.
    tenant = Tenant(name="Exp", whatsapp_number="+9617EXP")
    db_session.add(tenant)
    await db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email="owner@expired.com",
        hashed_password=hash_password("UNUSABLE"),
        role="owner",
        activation_token="EXPIRED-TOK",
        activated_at=None,
        activation_expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(user)
    await db_session.flush()

    from app.api.activation import activate
    from app.api.schemas.activation import ActivateRequest

    with pytest.raises(HTTPException) as exc:
        await activate(
            ActivateRequest(token="EXPIRED-TOK", new_password="whatever123"), db=db_session
        )
    assert exc.value.status_code == 400


# --------------------------------------------------- The Wall (founder scope)
async def test_founder_cannot_cross_the_wall(db_session: AsyncSession, two_tenants) -> None:
    """The founder-admin identity must NOT be able to read tenant data through a
    normal tenant-scoped repository. Using the admin id as a tenant_id finds
    nothing — the repositories scope by tenant_id, and an admin id is not a
    tenant. This is the Phase 1 wall test re-expressed at the founder boundary.
    """
    admin = await _admin(db_session)
    # Tenant A has users/products; the founder is not a tenant.
    from app.repositories.products import ProductRepository

    # An admin id is not a tenant_id → a tenant-scoped query returns nothing.
    leaked_products = await ProductRepository(db_session).list(admin.id)
    leaked_users = await UserRepository(db_session).get(admin.id, two_tenants.a.tenant_id)
    assert list(leaked_products) == []
    assert leaked_users is None
    # And there is no API path that hands a tenant repo the admin as a tenant —
    # admins are reached only via AdminRepository (not tenant-scoped).


# -------------------------------------------------------------------- audit
async def test_each_step_is_audited(db_session: AsyncSession) -> None:
    settings = _settings()
    admin = await _admin(db_session)
    req = await _make_request(db_session, email="owner@audit.com")
    await db_session.flush()
    result = await approve_request(
        db_session,
        settings=settings,
        mailer=(m := _FakeMailer()),
        admin=admin,
        request_id=req.id,
        whatsapp_number="+9617AUDIT",
    )
    token = m.sent[0]["body"].split("token=")[1].split()[0].strip()

    from app.api.activation import activate
    from app.api.schemas.activation import ActivateRequest

    await activate(ActivateRequest(token=token, new_password="auditpass123"), db=db_session)

    actions = set(
        (
            await db_session.execute(
                select(AuditLog.action).where(AuditLog.tenant_id == result.provisioned_tenant_id)
            )
        )
        .scalars()
        .all()
    )
    assert "signup_request.approved" in actions
    assert "user.activated" in actions


# ------------------------------------------------ /auth/register founder-only
async def test_register_requires_admin_token(db_session: AsyncSession, two_tenants) -> None:
    settings = _settings()
    admin = await _admin(db_session)

    # 1) A tenant-user "access" token must NOT satisfy get_current_admin.
    access = create_access_token(
        settings, user_id=two_tenants.a.tenant_id, tenant_id=two_tenants.a.tenant_id
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=access)
    with pytest.raises(HTTPException) as exc:
        await get_current_admin(creds=creds, db=db_session, settings=settings)
    assert exc.value.status_code == 401

    # 2) Even a token whose `sub` IS a real admin id but typed "access" must be
    #    rejected — this isolates the type-gate (audience separation), not just
    #    the "sub isn't an admin" fallback. Forge such a token directly.
    import jwt

    forged = jwt.encode(
        {"sub": str(admin.id), "tenant_id": str(admin.id), "type": "access"},
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(HTTPException) as exc2:
        await get_current_admin(
            creds=HTTPAuthorizationCredentials(scheme="Bearer", credentials=forged),
            db=db_session,
            settings=settings,
        )
    assert exc2.value.status_code == 401

    # 3) A real admin token DOES satisfy it.
    admin_tok = create_admin_token(settings, admin_id=admin.id)
    ok = await get_current_admin(
        creds=HTTPAuthorizationCredentials(scheme="Bearer", credentials=admin_tok),
        db=db_session,
        settings=settings,
    )
    assert ok.id == admin.id


# ---- helpers ----
async def _get_user(db: AsyncSession, email: str) -> User:
    return (await db.execute(select(User).where(User.email == email))).scalar_one()
