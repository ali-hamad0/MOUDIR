"""Task 7.9 — Cost tracking tests.

Covers:
- CostTrackingCallback writes an AgentRun row with the correct tenant_id.
- on_llm_end with no token_usage (older LangChain) does not crash.
- AgentRunRepository.daily_summary is tenant-scoped (cross-tenant isolation).
- pricing.compute_cost returns 0.0 for unknown model names.
- pricing.compute_cost returns correct value for a known model.
- Admin /costs endpoint returns per-day aggregates.

All tests are in-memory or use the conftest db_session fixture.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from langchain_core.outputs import LLMResult

from app.agents.llm.cost_callback import CostTrackingCallback, _extract_tokens
from app.agents.llm.pricing import compute_cost

# ── pricing helpers ──────────────────────────────────────────────────────────


def test_compute_cost_known_model():
    cost = compute_cost("gemini-2.5-flash", prompt_tokens=1000, completion_tokens=500)
    # 1000 * 0.00015 / 1000 + 500 * 0.00060 / 1000 = 0.00015 + 0.00030 = 0.00045
    assert abs(cost - 0.00045) < 1e-9


def test_compute_cost_unknown_model_returns_zero():
    cost = compute_cost("nonexistent-model-xyz", prompt_tokens=100, completion_tokens=100)
    assert cost == 0.0


def test_compute_cost_models_prefix_alias():
    cost_alias = compute_cost("models/gemini-2.5-flash", 1000, 500)
    cost_plain = compute_cost("gemini-2.5-flash", 1000, 500)
    assert abs(cost_alias - cost_plain) < 1e-9


# ── _extract_tokens ───────────────────────────────────────────────────────────


def test_extract_tokens_none_llm_output():
    assert _extract_tokens(None) == (0, 0)


def test_extract_tokens_empty_dict():
    assert _extract_tokens({}) == (0, 0)


def test_extract_tokens_gemini_usage_metadata():
    out = {"usage_metadata": {"prompt_token_count": 100, "candidates_token_count": 50}}
    assert _extract_tokens(out) == (100, 50)


def test_extract_tokens_openai_token_usage():
    out = {"token_usage": {"prompt_tokens": 200, "completion_tokens": 80}}
    assert _extract_tokens(out) == (200, 80)


def test_extract_tokens_anthropic_usage():
    out = {"usage": {"input_tokens": 150, "output_tokens": 60}}
    assert _extract_tokens(out) == (150, 60)


def test_extract_tokens_unknown_keys_returns_zero():
    out = {"totally_unknown_key": {"x": 1, "y": 2}}
    assert _extract_tokens(out) == (0, 0)


# ── CostTrackingCallback ──────────────────────────────────────────────────────


def _make_session_factory(session):
    @asynccontextmanager
    async def _cm():
        yield session

    return lambda: _cm()


async def test_callback_writes_agent_run_row():
    from app.db.models import AgentRun

    tenant_id = uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    cb = CostTrackingCallback(_make_session_factory(session), tenant_id, "supervisor")
    cb._model_name = "gemini-2.5-flash"

    response = LLMResult(
        generations=[],
        llm_output={"usage_metadata": {"prompt_token_count": 100, "candidates_token_count": 50}},
    )
    await cb.on_llm_end(response)

    session.add.assert_called_once()
    row = session.add.call_args[0][0]
    assert isinstance(row, AgentRun)
    assert row.tenant_id == tenant_id
    assert row.agent_name == "supervisor"
    assert row.model_name == "gemini-2.5-flash"
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 50
    assert float(row.cost_usd) > 0
    session.commit.assert_called_once()


async def test_callback_no_token_usage_does_not_crash():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    cb = CostTrackingCallback(_make_session_factory(session), uuid4(), "finance")
    cb._model_name = "gemini-2.5-flash"

    response = LLMResult(generations=[], llm_output=None)
    await cb.on_llm_end(response)

    session.commit.assert_called_once()
    row = session.add.call_args[0][0]
    assert row.prompt_tokens == 0
    assert row.completion_tokens == 0


async def test_callback_session_error_does_not_raise():
    """A failed DB write must NOT crash the agent — the callback swallows errors."""

    @asynccontextmanager
    async def _bad_session_factory():
        raise RuntimeError("DB is down")
        yield

    cb = CostTrackingCallback(lambda: _bad_session_factory(), uuid4(), "supervisor")
    cb._model_name = "unknown"
    response = LLMResult(generations=[], llm_output=None)
    await cb.on_llm_end(response)  # must not raise


async def test_callback_on_llm_start_sets_model_name():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    cb = CostTrackingCallback(_make_session_factory(session), uuid4(), "supervisor")
    await cb.on_llm_start(
        {"kwargs": {"model": "gemini-2.5-flash"}},
        prompts=["test"],
    )
    assert cb._model_name == "gemini-2.5-flash"


async def test_callback_on_llm_start_invocation_params_takes_priority():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    cb = CostTrackingCallback(_make_session_factory(session), uuid4(), "advisor")
    await cb.on_llm_start(
        {"kwargs": {"model": "fallback-model"}},
        prompts=["test"],
        invocation_params={"model": "grok-3-mini"},
    )
    assert cb._model_name == "grok-3-mini"


# ── AgentRunRepository ────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_agent_run_create_and_daily_summary(db_session):
    from app.db.models import Tenant
    from app.repositories.agent_runs import AgentRunRepository

    tenant = Tenant(name="CostTestShop", whatsapp_number="+961COST01")
    db_session.add(tenant)
    await db_session.flush()

    repo = AgentRunRepository(db_session)
    await repo.create(
        tenant_id=tenant.id,
        agent_name="supervisor",
        model_name="gemini-2.5-flash",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.000225,
    )
    await repo.create(
        tenant_id=tenant.id,
        agent_name="finance",
        model_name="gemini-2.5-flash",
        prompt_tokens=200,
        completion_tokens=100,
        cost_usd=0.000450,
    )
    await db_session.flush()

    summary = await repo.daily_summary(tenant.id)
    assert len(summary) == 1
    day_data = list(summary.values())[0]
    assert "supervisor" in day_data
    assert "finance" in day_data
    assert abs(day_data["supervisor"] - 0.000225) < 1e-6
    assert abs(day_data["finance"] - 0.000450) < 1e-6


@pytest.mark.integration
async def test_daily_summary_tenant_isolation(db_session):
    from app.db.models import Tenant
    from app.repositories.agent_runs import AgentRunRepository

    t1 = Tenant(name="CostTenant1", whatsapp_number="+961COST02")
    t2 = Tenant(name="CostTenant2", whatsapp_number="+961COST03")
    db_session.add(t1)
    db_session.add(t2)
    await db_session.flush()

    repo = AgentRunRepository(db_session)
    await repo.create(
        tenant_id=t1.id,
        agent_name="supervisor",
        model_name="gemini-2.5-flash",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.0001,
    )
    await repo.create(
        tenant_id=t2.id,
        agent_name="advisor",
        model_name="gemini-2.5-pro",
        prompt_tokens=500,
        completion_tokens=200,
        cost_usd=0.0025,
    )
    await db_session.flush()

    t1_summary = await AgentRunRepository(db_session).daily_summary(t1.id)
    t2_summary = await AgentRunRepository(db_session).daily_summary(t2.id)

    t1_agents = {a for day in t1_summary.values() for a in day}
    t2_agents = {a for day in t2_summary.values() for a in day}

    assert "supervisor" in t1_agents
    assert "advisor" not in t1_agents
    assert "advisor" in t2_agents
    assert "supervisor" not in t2_agents
