"""Task 8.7 — CostAlertTask unit tests.

Four cases:
(a) budget exceeded + webhook URL set → POST fired
(b) budget = 0 → no alert (no limit)
(c) two tenants — only the over-budget one fires
(d) last alert < 1h ago → no duplicate alert
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models import BusinessPolicy
from app.worker import CostAlertTask


def _make_policy(tenant_id, key, value) -> MagicMock:
    p = MagicMock(spec=BusinessPolicy)
    p.tenant_id = tenant_id
    p.key = key
    p.value = value
    return p


def _make_sessionmaker(policies_by_key: dict[str, BusinessPolicy | None], today_usd: float):
    """Return a minimal async_sessionmaker mock for CostAlertTask._check_and_alert."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.worker.BusinessPolicyRepository") as MockPolicyRepo,
        patch("app.worker.AgentRunRepository") as MockRunRepo,
    ):
        MockPolicyRepo.return_value.get_by_key = AsyncMock(
            side_effect=lambda tid, key: policies_by_key.get(key)
        )
        MockRunRepo.return_value.daily_summary = AsyncMock(
            return_value={} if today_usd == 0 else {"2026-06-09": {"supervisor": today_usd}}
        )

    sm = MagicMock()
    sm.return_value.__aenter__ = AsyncMock(return_value=session)
    sm.return_value.__aexit__ = AsyncMock(return_value=False)
    return sm, MockPolicyRepo, MockRunRepo


# ── (a) budget exceeded + webhook → POST fired ───────────────────────────────


@pytest.mark.asyncio
async def test_budget_exceeded_webhook_posted():
    tenant_id = uuid4()
    policies = {
        "daily_llm_budget_usd": _make_policy(tenant_id, "daily_llm_budget_usd", "5.00"),
        "cost_alert_last_sent": None,
        "alert_webhook_url": _make_policy(tenant_id, "alert_webhook_url", "http://hook.test/alert"),
    }

    task = CostAlertTask.__new__(CostAlertTask)

    with (
        patch("app.worker.BusinessPolicyRepository") as MockPR,
        patch("app.worker.AgentRunRepository") as MockAR,
        patch("app.worker.CostAlertTask._post_alert", new_callable=AsyncMock) as mock_post,
    ):
        MockPR.return_value.get_by_key = AsyncMock(side_effect=lambda tid, k: policies.get(k))
        MockAR.return_value.daily_summary = AsyncMock(
            return_value={"2026-06-09": {"supervisor": 7.50}}
        )

        session = AsyncMock()
        sm = MagicMock()
        sm.return_value.__aenter__ = AsyncMock(return_value=session)
        sm.return_value.__aexit__ = AsyncMock(return_value=False)
        task._sessionmaker = sm

        result = await task._check_and_alert(tenant_id)

    assert result is True
    mock_post.assert_awaited_once()
    _, call_tid, today_usd, budget_usd = mock_post.call_args[0]
    assert call_tid == tenant_id
    assert today_usd == pytest.approx(7.50)
    assert budget_usd == pytest.approx(5.00)


# ── (b) budget = 0 → no alert ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_zero_no_alert():
    tenant_id = uuid4()
    policies = {
        "daily_llm_budget_usd": _make_policy(tenant_id, "daily_llm_budget_usd", "0"),
    }

    task = CostAlertTask.__new__(CostAlertTask)

    with (
        patch("app.worker.BusinessPolicyRepository") as MockPR,
        patch("app.worker.AgentRunRepository"),
        patch("app.worker.CostAlertTask._post_alert", new_callable=AsyncMock) as mock_post,
    ):
        MockPR.return_value.get_by_key = AsyncMock(side_effect=lambda tid, k: policies.get(k))

        session = AsyncMock()
        sm = MagicMock()
        sm.return_value.__aenter__ = AsyncMock(return_value=session)
        sm.return_value.__aexit__ = AsyncMock(return_value=False)
        task._sessionmaker = sm

        result = await task._check_and_alert(tenant_id)

    assert result is False
    mock_post.assert_not_awaited()


# ── (c) two tenants — only over-budget one fires ─────────────────────────────


@pytest.mark.asyncio
async def test_only_over_budget_tenant_alerts():
    tid_over = uuid4()
    tid_under = uuid4()

    async def check_side_effect(tid):
        return tid == tid_over

    task = CostAlertTask.__new__(CostAlertTask)
    task._sessionmaker = MagicMock()

    with (
        patch.object(task, "_tenants_with_budget", new_callable=AsyncMock) as mock_tenants,
        patch.object(
            task, "_check_and_alert", new_callable=AsyncMock, side_effect=check_side_effect
        ) as mock_check,
    ):
        mock_tenants.return_value = [tid_over, tid_under]

        session = AsyncMock()
        sm = MagicMock()
        sm.return_value.__aenter__ = AsyncMock(return_value=session)
        sm.return_value.__aexit__ = AsyncMock(return_value=False)
        task._sessionmaker = sm

        alerts = await task.run_once()

    assert alerts == 1
    assert mock_check.await_count == 2


# ── (d) last alert < 1h ago → no duplicate ───────────────────────────────────


@pytest.mark.asyncio
async def test_no_duplicate_alert_within_one_hour():
    tenant_id = uuid4()
    recent = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    policies = {
        "daily_llm_budget_usd": _make_policy(tenant_id, "daily_llm_budget_usd", "5.00"),
        "cost_alert_last_sent": _make_policy(tenant_id, "cost_alert_last_sent", recent),
        "alert_webhook_url": _make_policy(tenant_id, "alert_webhook_url", "http://hook.test/alert"),
    }

    task = CostAlertTask.__new__(CostAlertTask)

    with (
        patch("app.worker.BusinessPolicyRepository") as MockPR,
        patch("app.worker.AgentRunRepository") as MockAR,
        patch("app.worker.CostAlertTask._post_alert", new_callable=AsyncMock) as mock_post,
    ):
        MockPR.return_value.get_by_key = AsyncMock(side_effect=lambda tid, k: policies.get(k))
        MockAR.return_value.daily_summary = AsyncMock(
            return_value={"2026-06-09": {"supervisor": 9.99}}
        )

        session = AsyncMock()
        sm = MagicMock()
        sm.return_value.__aenter__ = AsyncMock(return_value=session)
        sm.return_value.__aexit__ = AsyncMock(return_value=False)
        task._sessionmaker = sm

        result = await task._check_and_alert(tenant_id)

    assert result is False
    mock_post.assert_not_awaited()
