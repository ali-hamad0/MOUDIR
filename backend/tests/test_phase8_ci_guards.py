"""Phase 8 CI guard tests (Task 8.9).

Structural smoke-tests that verify every Phase 8 deliverable exists and is
wired correctly. These run in the normal pytest suite (no DB, no network) and
block the branch from merging if any deliverable is missing or broken.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# ── 1. GET /health/ai returns JSON with `available` key ──────────────────────


async def test_health_ai_endpoint_returns_available_key():
    from app.api import health

    app = FastAPI()
    app.include_router(health.router)

    mock_router = MagicMock()
    mock_router.is_healthy.return_value = True
    app.state.llm_router = mock_router

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health/ai")

    assert resp.status_code == 200
    body = resp.json()
    assert "available" in body
    assert body["available"] is True


# ── 2. POST /orders/manual requires authentication ────────────────────────────


async def test_manual_order_endpoint_requires_auth():
    from app.api.orders import router as orders_router
    from app.db.session import get_db_session

    app = FastAPI()
    app.include_router(orders_router)

    session = AsyncMock()

    async def _db():
        yield session

    app.dependency_overrides[get_db_session] = _db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/orders/manual",
            json={
                "customer_phone": "+96170000001",
                "items": [{"product_id": str(uuid4()), "quantity": 1}],
            },
        )

    assert resp.status_code == 401


# ── 3. RateLimiter accepts default_rpm argument ───────────────────────────────


def test_rate_limiter_accepts_default_rpm():
    from app.infra.rate_limiter import RateLimiter

    redis_mock = MagicMock()
    limiter = RateLimiter(redis_mock, default_rpm=60)
    assert limiter is not None


# ── 4. FallbackLLMRouter.is_healthy() exists and is callable ─────────────────


def test_fallback_llm_router_has_is_healthy():
    from app.agents.llm.router import FallbackLLMRouter

    assert hasattr(FallbackLLMRouter, "is_healthy"), "FallbackLLMRouter missing is_healthy()"
    assert callable(FallbackLLMRouter.is_healthy)

    mock_provider = MagicMock()
    mock_provider.is_healthy.return_value = True
    router = FallbackLLMRouter([mock_provider])
    result = router.is_healthy()
    assert isinstance(result, bool)


# ── 5. CostAlertTask exists in app.worker ────────────────────────────────────


def test_cost_alert_task_importable():
    from app.worker import CostAlertTask

    assert CostAlertTask is not None
    assert hasattr(CostAlertTask, "run_once")


# ── 6. Red-team golden set has ≥ 25 entries ──────────────────────────────────


def test_redteam_golden_set_has_minimum_entries():
    golden_dir = Path(__file__).parent.parent / "app" / "agents" / "eval" / "golden"
    redteam_file = golden_dir / "redteam.jsonl"
    assert redteam_file.exists(), "redteam.jsonl not found"

    entries = [
        json.loads(line)
        for line in redteam_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(entries) >= 25, f"redteam.jsonl has only {len(entries)} entries (need ≥ 25)"


# ── 7. Per-agent golden sets each have ≥ 20 entries ──────────────────────────


@pytest.mark.parametrize("agent", ["order", "inventory", "finance", "customer", "advisor"])
def test_per_agent_golden_set_has_minimum_entries(agent: str):
    golden_dir = Path(__file__).parent.parent / "app" / "agents" / "eval" / "golden"
    agent_file = golden_dir / f"{agent}.jsonl"
    assert agent_file.exists(), f"{agent}.jsonl not found"

    entries = [
        json.loads(line)
        for line in agent_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(entries) >= 20, f"{agent}.jsonl has only {len(entries)} entries (need ≥ 20)"
