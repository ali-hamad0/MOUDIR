"""WhatsApp one-time-code verification for public signup requests.

The public signup form (store name + phone + email) creates a `pending`
application. Before that application is accepted we prove the phone is real and
reachable: a 6-digit code is sent over WhatsApp and must be typed back.

There is NO tenant and NO account at this stage, so this cannot reuse the
TenantOwner verification machinery (that is tenant-scoped). All state lives in
Redis with TTLs:

  signup_otp:code:{phone}      sha256(code)   — the live code (TTL = ttl_seconds)
  signup_otp:cooldown:{phone}  "1"            — resend cooldown (short TTL)
  signup_otp:sends:{phone}     INCR counter   — per-hour send cap
  signup_otp:attempts:{phone}  INCR counter   — verify attempts against a code

Unlike the rate limiter (which fails OPEN — observability must never block real
traffic), this is a security gate and fails CLOSED: if Redis is unreachable we
refuse rather than wave an unverified number through.

The code itself is never logged and never returned in an API response — only
delivered to the phone (in dev mode WhatsApp logs the payload, so local/CI runs
read it from the structured log).
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import HTTPException, status
from redis.asyncio import Redis

from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.infra.whatsapp import WhatsAppClient
from prompts import signup_ar

log = get_logger(__name__)

_CODE_KEY = "signup_otp:code:{phone}"
_COOLDOWN_KEY = "signup_otp:cooldown:{phone}"
_SENDS_KEY = "signup_otp:sends:{phone}"
_ATTEMPTS_KEY = "signup_otp:attempts:{phone}"

_SECOND_PER_HOUR = 3600


def _hash_code(code: str) -> str:
    """Store only a hash of the code so a Redis dump never leaks live codes."""
    return hashlib.sha256(code.encode()).hexdigest()


class SignupOtpService:
    """Issue and verify signup phone OTPs over WhatsApp, backed by Redis."""

    def __init__(self, redis: Redis, whatsapp: WhatsAppClient, settings: Settings) -> None:
        self._redis = redis
        self._whatsapp = whatsapp
        self._settings = settings

    async def request_code(self, phone: str) -> None:
        """Generate, store, and WhatsApp a fresh code for `phone` (E.164).

        Enforces a resend cooldown and a per-hour send cap so the endpoint
        cannot be abused to flood a number with messages. Raises 429 when a cap
        is hit, 503 if Redis is unreachable (fail-closed).
        """
        s = self._settings
        cooldown_key = _COOLDOWN_KEY.format(phone=phone)
        sends_key = _SENDS_KEY.format(phone=phone)
        code_key = _CODE_KEY.format(phone=phone)
        attempts_key = _ATTEMPTS_KEY.format(phone=phone)

        try:
            if await self._redis.get(cooldown_key) is not None:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS, signup_ar.TOO_MANY_OTP_REQUESTS
                )

            sends = await self._redis.incr(sends_key)
            if sends == 1:
                await self._redis.expire(sends_key, _SECOND_PER_HOUR)
            if sends > s.signup_otp_max_sends_per_hour:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS, signup_ar.TOO_MANY_OTP_REQUESTS
                )

            # A fresh code voids any previous one and resets its attempt budget.
            code = "".join(secrets.choice("0123456789") for _ in range(s.signup_otp_length))
            await self._redis.set(code_key, _hash_code(code), ex=s.signup_otp_ttl_seconds)
            await self._redis.delete(attempts_key)
            await self._redis.set(cooldown_key, "1", ex=s.signup_otp_resend_cooldown_seconds)
        except HTTPException:
            raise
        except Exception as exc:
            log.error("signup_otp.redis_error", op="request", error=str(exc))
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, signup_ar.OTP_SERVICE_UNAVAILABLE
            ) from exc

        # Send AFTER state is committed so a delivery failure can't desync the
        # stored code from what the user holds. The code is never logged here.
        await self._whatsapp.send_text(phone, signup_ar.OTP_MESSAGE.format(code=code))
        log.info("signup_otp.sent", phone_suffix=phone[-4:])

    async def verify_code(self, phone: str, code: str) -> bool:
        """Return True iff `code` matches the live code for `phone`.

        A correct code is single-use (consumed on success). Each wrong attempt
        is counted; once the attempt cap is hit the code is burned so it cannot
        be brute-forced — the user must request a new one. Raises 503 on a Redis
        outage (fail-closed: an unverifiable number is never accepted).
        """
        code_key = _CODE_KEY.format(phone=phone)
        attempts_key = _ATTEMPTS_KEY.format(phone=phone)

        try:
            stored = await self._redis.get(code_key)
            if stored is None:
                return False  # no code issued, or it expired

            # Count the attempt first; burn the code once the budget is spent.
            attempts = await self._redis.incr(attempts_key)
            if attempts == 1:
                await self._redis.expire(attempts_key, self._settings.signup_otp_ttl_seconds)
            if attempts > self._settings.signup_otp_max_attempts:
                await self._redis.delete(code_key)
                return False

            # Constant-time compare on the hashes so a wrong code leaks no timing.
            if not secrets.compare_digest(stored, _hash_code(code)):
                return False

            # Correct: consume the code and its counters so it can't be replayed.
            await self._redis.delete(code_key, attempts_key)
            log.info("signup_otp.verified", phone_suffix=phone[-4:])
            return True
        except Exception as exc:
            log.error("signup_otp.redis_error", op="verify", error=str(exc))
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, signup_ar.OTP_SERVICE_UNAVAILABLE
            ) from exc
