from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from passlib.context import CryptContext

from app.infra.settings import Settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(settings: Settings, *, user_id: UUID, tenant_id: UUID) -> str:
    """Sign a short-lived access token.

    The token CARRIES tenant_id as a claim, but the database query still filters
    by tenant_id independently — the claim is never the trust anchor (Wall rule).
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
        "type": "access",
    }
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def create_admin_token(settings: Settings, *, admin_id: UUID) -> str:
    """Sign a short-lived FOUNDER token.

    Distinct `type: "admin"` is the audience separation that keeps the two
    identities apart: a tenant-user dependency rejects this token, and the admin
    dependency rejects an "access" token. A founder token carries NO tenant_id —
    it is above all tenants and never used to scope a tenant query.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
        "type": "admin",
    }
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(settings: Settings, token: str) -> dict:
    """Decode + verify a token. Raises jwt.PyJWTError on bad/expired tokens.

    Does NOT check the `type` claim — callers (get_current_user / get_current_admin)
    enforce the expected type so the two identities can never be confused.
    """
    return jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )
