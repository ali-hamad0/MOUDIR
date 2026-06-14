"""Signup phone-OTP verification (registration phone check).

Covers the Lebanese mobile normalizer, the Redis-backed SignupOtpService
(issue / verify / abuse caps), and the public endpoints — that a code must be
sent and matched before a signup request is created, and that garbage numbers
and reused/expired codes are rejected.

Offline: fakeredis stands in for Redis and the WhatsApp client is mocked, so
nothing hits the network (mirrors the rate-limiter and manual-order tests).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.signup_requests import router as signup_router
from app.db.models import SignupRequest
from app.db.session import get_db_session
from app.infra.phone import normalize_lebanese_mobile
from app.infra.settings import Settings, get_settings
from app.services.signup_otp import SignupOtpService

# ── helpers ─────────────────────────────────────────────────────────────────


def _settings(**overrides) -> Settings:
    base = dict(
        signup_otp_length=6,
        signup_otp_ttl_seconds=300,
        signup_otp_resend_cooldown_seconds=60,
        signup_otp_max_sends_per_hour=5,
        signup_otp_max_attempts=5,
    )
    base.update(overrides)
    return Settings.model_construct(**base)


def _make_app(redis, whatsapp, *, settings=None, db_override=None) -> FastAPI:
    app = FastAPI()
    app.include_router(signup_router)
    app.state.redis = redis
    app.state.whatsapp_client = whatsapp
    app.dependency_overrides[get_settings] = lambda: settings or _settings()
    if db_override is not None:
        app.dependency_overrides[get_db_session] = db_override
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _code_from_whatsapp(whatsapp: AsyncMock) -> str:
    """Pull the digits the service actually sent over WhatsApp."""
    body = whatsapp.send_text.call_args.args[1]
    return re.search(r"\d{4,}", body).group(0)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def whatsapp():
    wa = MagicMock()
    wa.send_text = AsyncMock()
    return wa


# ── phone normalizer ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("03 234567", "+9613234567"),
        ("70123456", "+96170123456"),
        ("+961 3 234567", "+9613234567"),
        ("009613234567", "+9613234567"),
        ("‎03-234-567", "+9613234567"),  # bidi mark + dashes from a paste
        ("81234567", "+96181234567"),
    ],
)
def test_normalize_accepts_lebanese_mobiles(raw, expected):
    assert normalize_lebanese_mobile(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "123",
        "0412345",  # landline prefix, not mobile
        "0399999999999",  # too long
        "+1 555 123 4567",  # not Lebanese
        "abc",
    ],
)
def test_normalize_rejects_non_mobiles(raw):
    assert normalize_lebanese_mobile(raw) is None


# ── SignupOtpService ────────────────────────────────────────────────────────


async def test_request_code_sends_and_verifies(redis, whatsapp):
    svc = SignupOtpService(redis, whatsapp, _settings())
    await svc.request_code("+9613234567")

    whatsapp.send_text.assert_awaited_once()
    code = _code_from_whatsapp(whatsapp)
    assert len(code) == 6

    assert await svc.verify_code("+9613234567", code) is True
    # Single-use: a second verify with the same code fails (it was consumed).
    assert await svc.verify_code("+9613234567", code) is False


async def test_verify_rejects_wrong_code(redis, whatsapp):
    svc = SignupOtpService(redis, whatsapp, _settings())
    await svc.request_code("+9613234567")
    assert await svc.verify_code("+9613234567", "000000") is False


async def test_verify_with_no_code_is_false(redis, whatsapp):
    svc = SignupOtpService(redis, whatsapp, _settings())
    assert await svc.verify_code("+9613234567", "123456") is False


async def test_attempt_cap_burns_the_code(redis, whatsapp):
    svc = SignupOtpService(redis, whatsapp, _settings(signup_otp_max_attempts=3))
    await svc.request_code("+9613234567")
    code = _code_from_whatsapp(whatsapp)

    for _ in range(3):
        assert await svc.verify_code("+9613234567", "999999") is False
    # Cap spent → the code is burned even though it's the correct one.
    assert await svc.verify_code("+9613234567", code) is False


async def test_resend_cooldown_blocks_immediate_resend(redis, whatsapp):
    from fastapi import HTTPException

    svc = SignupOtpService(redis, whatsapp, _settings())
    await svc.request_code("+9613234567")
    with pytest.raises(HTTPException) as exc:
        await svc.request_code("+9613234567")
    assert exc.value.status_code == 429


async def test_hourly_send_cap(redis, whatsapp):
    from fastapi import HTTPException

    svc = SignupOtpService(redis, whatsapp, _settings(signup_otp_max_sends_per_hour=2))
    phone = "+9613234567"
    for _ in range(2):
        await svc.request_code(phone)
        await redis.delete(f"signup_otp:cooldown:{phone}")  # isolate the hourly cap
    with pytest.raises(HTTPException) as exc:
        await svc.request_code(phone)
    assert exc.value.status_code == 429


# ── /signup-requests/otp endpoint ───────────────────────────────────────────


async def test_otp_endpoint_sends_and_normalizes(redis, whatsapp):
    app = _make_app(redis, whatsapp)
    async with _client(app) as c:
        res = await c.post("/signup-requests/otp", json={"owner_phone": "03 234567"})
    assert res.status_code == 200
    assert res.json()["sent_to"] == "+9613234567"
    whatsapp.send_text.assert_awaited_once()
    # The code is never echoed in the response.
    assert "234567" not in str(res.json().get("code", ""))


async def test_otp_endpoint_rejects_invalid_phone(redis, whatsapp):
    app = _make_app(redis, whatsapp)
    async with _client(app) as c:
        res = await c.post("/signup-requests/otp", json={"owner_phone": "12345"})
    assert res.status_code == 400
    whatsapp.send_text.assert_not_awaited()


async def test_otp_endpoint_cooldown_returns_429(redis, whatsapp):
    app = _make_app(redis, whatsapp)
    async with _client(app) as c:
        first = await c.post("/signup-requests/otp", json={"owner_phone": "03234567"})
        second = await c.post("/signup-requests/otp", json={"owner_phone": "03234567"})
    assert first.status_code == 200
    assert second.status_code == 429


# ── POST /signup-requests (gated on OTP) ────────────────────────────────────


async def test_create_rejects_bad_otp_without_touching_db(redis, whatsapp):
    # The DB override must never be called: a bad code is rejected before any read.
    db = AsyncMock()
    app = _make_app(redis, whatsapp, db_override=lambda: db)
    async with _client(app) as c:
        res = await c.post(
            "/signup-requests",
            json={
                "business_name": "Dekkenet Ali",
                "owner_phone": "03234567",
                "owner_email": "ali@example.com",
                "otp_code": "000000",
            },
        )
    assert res.status_code == 400
    db.execute.assert_not_called()
    db.commit.assert_not_called()


async def test_create_succeeds_with_valid_otp(redis, whatsapp):
    phone = "+9613234567"
    settings = _settings()
    svc = SignupOtpService(redis, whatsapp, settings)
    await svc.request_code(phone)
    code = _code_from_whatsapp(whatsapp)

    db = AsyncMock()

    async def _add(row: SignupRequest) -> SignupRequest:
        row.id = uuid4()
        row.created_at = datetime.now(UTC)
        return row

    repo = MagicMock()
    repo.get_pending_by_email = AsyncMock(return_value=None)
    repo.add = AsyncMock(side_effect=_add)

    app = _make_app(redis, whatsapp, settings=settings, db_override=lambda: db)
    with patch("app.api.signup_requests.SignupRequestRepository", return_value=repo):
        async with _client(app) as c:
            res = await c.post(
                "/signup-requests",
                json={
                    "business_name": "Dekkenet Ali",
                    "owner_phone": "03 234567",
                    "owner_email": "ali@example.com",
                    "otp_code": code,
                },
            )

    assert res.status_code == 201
    body = res.json()
    assert body["business_name"] == "Dekkenet Ali"
    assert body["status"] == "pending"
    # The stored phone is the normalized E.164 form, not the raw input.
    repo.add.assert_awaited_once()
    assert repo.add.call_args.args[0].owner_phone == phone
    db.commit.assert_awaited_once()
