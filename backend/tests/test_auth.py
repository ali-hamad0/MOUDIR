"""JWT behavior: claim round-trip, tenant-claim tampering, and expiry.

The tenant_id in a token is a claim, never the trust anchor — get_current_user
loads the user inside the claimed scope, so a tampered tenant_id finds nothing.
"""

from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.infra.security import create_access_token, decode_access_token
from app.infra.settings import Settings
from app.repositories.users import UserRepository
from tests.conftest import TwoTenants


def _settings(expiry_minutes: int = 15) -> Settings:
    return Settings.model_construct(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        jwt_expiry_minutes=expiry_minutes,
    )


def test_token_round_trip_carries_claims() -> None:
    settings = _settings()
    uid, tid = uuid4(), uuid4()
    token = create_access_token(settings, user_id=uid, tenant_id=tid)
    decoded = decode_access_token(settings, token)
    assert decoded["sub"] == str(uid)
    assert decoded["tenant_id"] == str(tid)
    assert decoded["type"] == "access"


async def test_tampered_tenant_id_is_rejected(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    settings = _settings()
    # Real user in tenant A.
    user = await UserRepository(db_session).get_by_email(
        two_tenants.a.tenant_id, two_tenants.a.user_email
    )
    assert user is not None

    # Forge a token claiming the user belongs to tenant B (a different tenant).
    forged = create_access_token(settings, user_id=user.id, tenant_id=two_tenants.b.tenant_id)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=forged)
    # The scoped lookup in tenant B finds no such user → 401.
    with pytest.raises(HTTPException) as exc:
        await get_current_user(creds=creds, db=db_session, settings=settings)
    assert exc.value.status_code == 401


async def test_valid_token_loads_user(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    settings = _settings()
    user = await UserRepository(db_session).get_by_email(
        two_tenants.a.tenant_id, two_tenants.a.user_email
    )
    assert user is not None
    token = create_access_token(settings, user_id=user.id, tenant_id=two_tenants.a.tenant_id)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    loaded: User = await get_current_user(creds=creds, db=db_session, settings=settings)
    assert loaded.id == user.id
    assert loaded.tenant_id == two_tenants.a.tenant_id


async def test_expired_token_is_rejected(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    # Sign with a negative expiry so the token is already expired.
    expired_settings = _settings(expiry_minutes=-1)
    user = await UserRepository(db_session).get_by_email(
        two_tenants.a.tenant_id, two_tenants.a.user_email
    )
    assert user is not None
    token = create_access_token(
        expired_settings, user_id=user.id, tenant_id=two_tenants.a.tenant_id
    )

    # Decoding directly raises ExpiredSignatureError.
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(_settings(), token)

    # And get_current_user turns that into a 401.
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(creds=creds, db=db_session, settings=_settings())
    assert exc.value.status_code == 401
