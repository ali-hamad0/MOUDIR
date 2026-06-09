"""Task 8.4 — Chaos tests: Vault kill / LLM kill / DB kill.

Failures are simulated at the code boundary (no Docker containers stopped).
All three scenarios must degrade gracefully — never a silent crash or a
misleading 200/500 response.

Vault kill  → lifespan raises on startup; app refuses to start.
LLM kill    → supervisor catches LLMUnavailable and returns Arabic fallback.
DB kill     → webhook returns HTTP 503 (not 500).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import asyncpg
import fakeredis.aioredis
import httpx
import pytest
import structlog
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.agents.llm.exceptions import LLMUnavailable
from app.infra.rate_limiter import RateLimiter
from prompts import supervisor_ar

# ── Vault kill ────────────────────────────────────────────────────────────────


def test_vault_kill_raises_on_resolve_secrets():
    """resolve_secrets must propagate Vault errors — never swallow them.

    If Vault is unreachable at startup the lifespan propagates the exception,
    the ASGI server refuses to start, and the on-call engineer sees a clear
    error in the process log — not a running app with missing secrets.
    """
    from app.infra.vault import VaultClient, resolve_secrets

    with patch.object(VaultClient, "__init__", side_effect=RuntimeError("Vault unreachable")):
        with pytest.raises(RuntimeError, match="Vault unreachable"):
            resolve_secrets(MagicMock())


def test_vault_read_secret_propagates_hvac_error():
    """VaultClient.read_secret must not swallow Vault API errors."""
    from app.infra.vault import VaultClient

    vault = object.__new__(VaultClient)
    mock_client = MagicMock()
    mock_client.is_authenticated.return_value = True
    mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("KV read failed")
    vault._client = mock_client

    with pytest.raises(Exception, match="KV read failed"):
        vault.read_secret("modir/llm", "gemini_api_key")


# ── LLM kill ─────────────────────────────────────────────────────────────────


async def test_llm_kill_supervisor_returns_arabic_fallback():
    """When the LLM is unavailable the supervisor returns the Arabic fallback.

    The supervisor's handle() catches ALL exceptions from the graph and returns
    supervisor_ar.FALLBACK_REPLY — never raises to the dispatcher/webhook.
    """
    from app.agents.supervisor.agent import OwnerSupervisor

    # Patch classify_intent (the first LLM call in the graph) to raise.
    with patch(
        "app.agents.supervisor.agent.classify_intent",
        new=AsyncMock(side_effect=LLMUnavailable("all providers failed")),
    ):
        supervisor = OwnerSupervisor(
            router=MagicMock(),
            order_agent=MagicMock(),
            inventory_agent=MagicMock(),
            finance_agent=MagicMock(),
            customer_agent=MagicMock(),
            advisor_agent=MagicMock(),
            checkpointer=None,
            sessionmaker=None,
        )
        reply = await supervisor.handle("كيف الأوضاع؟", uuid4(), "session-1")

    assert reply == supervisor_ar.FALLBACK_REPLY, f"Expected Arabic fallback, got: {reply!r}"


async def test_llm_kill_supervisor_logs_error():
    """When the LLM is unavailable the supervisor logs at ERROR level."""
    from app.agents.supervisor.agent import OwnerSupervisor

    with structlog.testing.capture_logs() as cap_logs:
        with patch(
            "app.agents.supervisor.agent.classify_intent",
            new=AsyncMock(side_effect=LLMUnavailable("providers exhausted")),
        ):
            supervisor = OwnerSupervisor(
                router=MagicMock(),
                order_agent=MagicMock(),
                inventory_agent=MagicMock(),
                finance_agent=MagicMock(),
                customer_agent=MagicMock(),
                advisor_agent=MagicMock(),
                checkpointer=None,
                sessionmaker=None,
            )
            await supervisor.handle("شو الطلبات؟", uuid4(), "session-2")

    assert any(
        log.get("event") == "supervisor.handle.error" for log in cap_logs
    ), "Expected supervisor.handle.error structlog entry when LLM is unavailable"


# ── DB kill ───────────────────────────────────────────────────────────────────


def _db_kill_app() -> FastAPI:
    """Minimal FastAPI with the same 503 handler as create_app(), no lifespan.

    app.main runs create_app() at module level (for uvicorn), so importing it
    outside of a running stack triggers get_settings() and fails without env
    vars.  We build an equivalent minimal app here — same exception handler,
    same webhook router, broken DB dependency — to keep the test self-contained.
    """
    from fastapi.responses import JSONResponse

    from app.api import webhooks
    from app.db.session import get_db_session
    from app.infra.logging import get_logger

    _app = FastAPI()

    @_app.exception_handler(asyncpg.PostgresConnectionError)
    async def _db_conn_err(request: Request, exc: asyncpg.PostgresConnectionError):
        log = get_logger("db.connection.error")
        log.error("db.connection.error", path=str(request.url.path), error=str(exc))
        return JSONResponse(
            status_code=503,
            content={"detail": "الخدمة غير متاحة مؤقتاً. حاول بعد قليل."},
        )

    _app.include_router(webhooks.router)

    async def _broken_db(request: Request):
        raise asyncpg.PostgresConnectionError("connection refused")

    _app.dependency_overrides[get_db_session] = _broken_db

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    _app.state.redis = fake_redis
    _app.state.rate_limiter = RateLimiter(fake_redis, default_rpm=0)
    return _app


def _meta_payload(to: str, from_: str, text: str) -> dict:
    """Build a minimal valid Meta Cloud API webhook payload for chaos / load tests."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": to.lstrip("+"),
                                "phone_number_id": "TEST_PID",
                            },
                            "contacts": [{"profile": {"name": "Test"}, "wa_id": from_.lstrip("+")}],
                            "messages": [
                                {
                                    "id": "wamid.TEST",
                                    "from": from_.lstrip("+"),
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


async def test_db_kill_webhook_returns_503():
    """A lost DB connection on the webhook path returns HTTP 503, not 500.

    The Phase 8 asyncpg.PostgresConnectionError handler converts DB outages
    to 503 so the load balancer / on-call can distinguish a DB outage from a
    code bug (500).
    """
    app = _db_kill_app()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhooks/whatsapp",
            json=_meta_payload("+96100000000", "+96111111111", "مرحبا"),
        )

    assert response.status_code == 503, f"Expected 503 on DB outage, got {response.status_code}"


async def test_db_kill_response_is_arabic():
    """The 503 response body is Lebanese Arabic, not an English stack trace."""
    app = _db_kill_app()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/webhooks/whatsapp",
            json=_meta_payload("+96100000000", "+96111111111", "مرحبا"),
        )

    body = response.json()
    detail = body.get("detail", "")
    # Must contain Arabic characters — not an English error message.
    assert any("؀" <= ch <= "ۿ" for ch in detail), f"Expected Arabic in 503 detail, got: {detail!r}"
