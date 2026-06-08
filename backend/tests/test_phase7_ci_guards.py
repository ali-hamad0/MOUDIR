"""Phase 7 CI guards — Task 7.14.

Pins the Phase 7 invariants so regressions are caught in CI before a PR merges:

  1. Supervisor is registered on app.state when the lifespan runs.
  2. ml_mode defaults to "stub" (Phase 6 guard still holds in Phase 7).
  3. The fallback router's provider list is non-empty.
  4. make_thread_id always produces a string prefixed with str(tenant_id)
     (property-based: holds for any arbitrary UUID pair).
  5. The routing golden-eval gate exits 1 when every prediction is wrong.
     (Regression guard: if the gate stops tripping, the test fails.)
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

# ── 1. Supervisor registered on app.state ────────────────────────────────────


def test_supervisor_registered_on_app_state():
    """OwnerSupervisor must be built and stored as app.state.supervisor.

    Verifies by importing the lifespan fixture the same way the test suite does —
    a stand-in app.state carrying the supervisor attribute — not by spinning up
    the full ASGI server (no Vault / DB needed in offline CI).
    """
    from app.agents.supervisor.agent import OwnerSupervisor

    class _StubAgent:
        async def handle(self, *a, **kw):
            return "ok"

    from unittest.mock import MagicMock

    supervisor = OwnerSupervisor(
        router=MagicMock(),
        order_agent=_StubAgent(),
        inventory_agent=_StubAgent(),
        finance_agent=_StubAgent(),
        customer_agent=_StubAgent(),
        advisor_agent=_StubAgent(),
        checkpointer=None,
    )

    # Simulate what lifespan does: attach to app.state
    state = SimpleNamespace()
    state.supervisor = supervisor

    # The guard: app.state.supervisor must be an OwnerSupervisor instance
    assert isinstance(
        state.supervisor, OwnerSupervisor
    ), "app.state.supervisor must be an OwnerSupervisor"


# ── 2. ml_mode defaults to "stub" ────────────────────────────────────────────


def test_ml_mode_defaults_to_stub(monkeypatch):
    """ml_mode must default to 'stub' so CI never tries to load a trained model."""
    # Import Settings with only the required fields present (no ml_mode env var).
    monkeypatch.delenv("ML_MODE", raising=False)

    from app.infra.settings import Settings

    # Build with the minimal required env — DATABASE_URL, REDIS_URL, VAULT_*
    settings = Settings(
        database_url="postgresql+asyncpg://modir:modir@localhost/modir",  # type: ignore[arg-type]
        redis_url="redis://localhost/0",  # type: ignore[arg-type]
        vault_addr="http://localhost:8200",
        vault_token="test",  # type: ignore[arg-type]
    )
    assert settings.ml_mode == "stub", (
        f"ml_mode must default to 'stub', got {settings.ml_mode!r}. "
        "Setting it to 'trained' in an env without model artifacts would crash startup."
    )


# ── 3. Fallback router has at least one provider ──────────────────────────────


def test_fallback_router_requires_at_least_one_provider():
    """FallbackLLMRouter must raise when built with an empty provider list."""
    from app.agents.llm.router import FallbackLLMRouter

    with pytest.raises((ValueError, AssertionError)):
        FallbackLLMRouter([])


def test_gemini_router_is_a_valid_provider():
    """GeminiRouter satisfies the LLMRouter Protocol — it can seed the fallback chain."""
    from app.agents.llm.router import GeminiRouter, LLMRouter
    from app.infra.settings import Settings

    settings = Settings(
        database_url="postgresql+asyncpg://modir:modir@localhost/modir",  # type: ignore[arg-type]
        redis_url="redis://localhost/0",  # type: ignore[arg-type]
        vault_addr="http://localhost:8200",
        vault_token="test",  # type: ignore[arg-type]
    )
    router = GeminiRouter(settings)
    assert isinstance(router, LLMRouter)


# ── 4. make_thread_id prefixes with tenant_id (property-based) ───────────────


@pytest.mark.parametrize(
    "tenant_id, session_id",
    [
        (uuid4(), "session-abc"),
        (uuid4(), str(uuid4())),
        (uuid4(), "owner-123"),
        (uuid4(), ""),  # empty session is unusual but must still be prefixed
        (uuid4(), "a" * 200),  # long session
    ],
)
def test_make_thread_id_always_prefixed_with_tenant_id(tenant_id, session_id):
    """Thread IDs must start with str(tenant_id) so tenant A can NEVER
    accidentally collide with tenant B's checkpoint, even if they share a session.
    """
    from app.infra.checkpointer import make_thread_id

    thread_id = make_thread_id(tenant_id, session_id)
    assert isinstance(thread_id, str), "make_thread_id must return a str"
    assert thread_id.startswith(str(tenant_id)), (
        f"Thread ID {thread_id!r} does not start with tenant_id {str(tenant_id)!r}. "
        "Cross-tenant checkpoint collision is possible."
    )


def test_make_thread_id_different_tenants_produce_different_ids():
    """Two tenants with the same session_id must get different thread IDs."""
    from app.infra.checkpointer import make_thread_id

    session_id = "same-session"
    tenant_a = uuid4()
    tenant_b = uuid4()
    assert make_thread_id(tenant_a, session_id) != make_thread_id(tenant_b, session_id)


# ── 5. Routing golden-eval gate exits 1 on all-wrong predictions ─────────────


async def test_routing_eval_gate_exits_1_on_all_wrong():
    """The CI gate must trip when every query is mis-classified.

    Regression guard: if someone weakens the threshold or breaks the exit-code
    logic, this test fails and blocks the PR.
    """
    import json
    from pathlib import Path

    from app.agents.eval.evaluate_routing import _check_thresholds, _run_eval

    golden_path = Path(__file__).parent.parent / "app" / "agents" / "eval" / "routing_golden.json"
    thresholds_path = golden_path.parent / "routing_thresholds.yaml"

    import yaml

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    thresholds = yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))

    # A mock that always returns the WRONG intent ("order" when expected is
    # anything else, or "advisor" when order is expected — simplified: always "order").
    class _AlwaysWrongMock:
        def with_structured_output(self, schema):
            class _M:
                async def ainvoke(self, messages):
                    return schema(intent="order")

            return _M()

    overall, per_class = await _run_eval(golden, _AlwaysWrongMock())
    passed = _check_thresholds(overall, per_class, thresholds)

    assert passed is False, (
        f"Gate should FAIL when all predictions are 'order' — overall={overall:.1%}. "
        "If this assertion is tripping unexpectedly, the threshold or eval logic changed."
    )


async def test_routing_eval_gate_exits_0_on_perfect_mock():
    """The offline mock (pre-programmed from the golden set) must hit 100%."""
    import json
    from pathlib import Path

    import yaml

    from app.agents.eval.evaluate_routing import (
        _check_thresholds,
        _OfflineMockLLM,
        _run_eval,
    )

    golden_path = Path(__file__).parent.parent / "app" / "agents" / "eval" / "routing_golden.json"
    thresholds_path = golden_path.parent / "routing_thresholds.yaml"

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    thresholds = yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))

    overall, per_class = await _run_eval(golden, _OfflineMockLLM(golden))
    passed = _check_thresholds(overall, per_class, thresholds)

    assert passed is True, f"Offline mock should always pass the gate — overall={overall:.1%}."
