"""Task 7.6 — OwnerSupervisor routing tests.

Unit tests cover:
  - classify_intent (each intent class; LLM failure → advisor fallback; invalid string → advisor)
  - OwnerSupervisor routing (each intent routes to correct agent node; ambiguity fallback)
  - Supervisor compiles without checkpointer (offline CI)
  - handle() returns a string on success
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.supervisor.routing import classify_intent

# ── classify_intent ────────────────────────────────────────────────────────────


class TestClassifyIntent:
    async def _run(self, intent_str: str) -> str:
        from app.agents.supervisor.routing import _IntentClassification

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = _IntentClassification(intent=intent_str)
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_model
        return await classify_intent("أي سؤال", mock_llm)

    async def test_order_intent(self):
        assert await self._run("order") == "order"

    async def test_inventory_intent(self):
        assert await self._run("inventory") == "inventory"

    async def test_finance_intent(self):
        assert await self._run("finance") == "finance"

    async def test_customer_intent(self):
        assert await self._run("customer") == "customer"

    async def test_advisor_intent(self):
        assert await self._run("advisor") == "advisor"

    async def test_llm_exception_falls_back_to_advisor(self):
        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = RuntimeError("provider down")
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_model
        result = await classify_intent("أي سؤال", mock_llm)
        assert result == "advisor"

    async def test_validation_error_falls_back_to_advisor(self):
        from pydantic import ValidationError

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = ValidationError.from_exception_data(
            "test", [], input_type="python"
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_model
        result = await classify_intent("أي سؤال", mock_llm)
        assert result == "advisor"


# ── helpers for supervisor tests ───────────────────────────────────────────────


def _make_mock_agent(reply: str = "رد تجريبي") -> AsyncMock:
    agent = MagicMock()
    agent.handle = AsyncMock(return_value=reply)
    return agent


def _build_supervisor(intent_result: str = "advisor"):
    """Build an OwnerSupervisor with mock agents and a mock router."""
    from app.agents.supervisor.agent import OwnerSupervisor

    order_agent = _make_mock_agent("رد order")
    inventory_agent = _make_mock_agent("رد inventory")
    # The inventory node tries the stock-edit propose step first (Phase 10);
    # None means "not an edit" → falls through to the read-only handle().
    inventory_agent.propose_adjustment = AsyncMock(return_value=None)
    finance_agent = _make_mock_agent("رد finance")
    customer_agent = _make_mock_agent("رد customer")
    advisor_agent = _make_mock_agent("رد advisor")

    mock_llm = AsyncMock()
    mock_router = MagicMock()
    mock_router.tier1.return_value = mock_llm

    supervisor = OwnerSupervisor(
        router=mock_router,
        order_agent=order_agent,
        inventory_agent=inventory_agent,
        finance_agent=finance_agent,
        customer_agent=customer_agent,
        advisor_agent=advisor_agent,
        checkpointer=None,  # offline, no Postgres
    )
    return supervisor, {
        "order": order_agent,
        "inventory": inventory_agent,
        "finance": finance_agent,
        "customer": customer_agent,
        "advisor": advisor_agent,
    }


# ── routing per intent ─────────────────────────────────────────────────────────


class TestSupervisorRouting:
    """For each intent class, verify the correct specialist agent is called."""

    async def _route_and_check(self, intent: str, expected_agent_key: str):
        supervisor, agents = _build_supervisor()
        tenant_id = uuid.uuid4()

        with patch(
            "app.agents.supervisor.agent.classify_intent",
            new=AsyncMock(return_value=intent),
        ):
            reply = await supervisor.handle("سؤال تجريبي", tenant_id, "session-1")

        # The agent node that matches this intent should have been called once.
        # Order intent routes to advisor (Phase 7 design — see agent.py docstring).
        called_key = "advisor" if intent == "order" else intent
        agents[called_key].handle.assert_called_once()
        # No other agent should have been called.
        for key, agent in agents.items():
            if key != called_key:
                agent.handle.assert_not_called()
        assert isinstance(reply, str)

    async def test_order_intent_routes_to_advisor(self):
        await self._route_and_check("order", "advisor")

    async def test_inventory_intent_routes_to_inventory_agent(self):
        await self._route_and_check("inventory", "inventory")

    async def test_finance_intent_routes_to_finance_agent(self):
        await self._route_and_check("finance", "finance")

    async def test_customer_intent_routes_to_customer_agent(self):
        await self._route_and_check("customer", "customer")

    async def test_advisor_intent_routes_to_advisor_agent(self):
        await self._route_and_check("advisor", "advisor")

    async def test_ambiguous_intent_fallback_routes_to_advisor(self):
        """classify_intent returns "advisor" for ambiguous input → advisor called."""
        supervisor, agents = _build_supervisor()
        tenant_id = uuid.uuid4()

        with patch(
            "app.agents.supervisor.agent.classify_intent",
            new=AsyncMock(return_value="advisor"),
        ):
            reply = await supervisor.handle("فيك تساعدني؟", tenant_id, "session-2")

        agents["advisor"].handle.assert_called_once()
        assert isinstance(reply, str)


# ── handle() contract ─────────────────────────────────────────────────────────


class TestSupervisorHandle:
    async def test_handle_returns_string(self):
        supervisor, _ = _build_supervisor()
        tenant_id = uuid.uuid4()

        with patch(
            "app.agents.supervisor.agent.classify_intent",
            new=AsyncMock(return_value="advisor"),
        ):
            result = await supervisor.handle("كيف محلي؟", tenant_id, "sess-abc")

        assert isinstance(result, str)
        assert len(result) > 0

    async def test_handle_passes_tenant_id_to_agent(self):
        """The Wall: the agent node receives exactly the supervisor's tenant_id."""
        supervisor, agents = _build_supervisor()
        tenant_id = uuid.uuid4()
        seen_tenant: list = []

        async def _spy_handle(question, tid):
            seen_tenant.append(tid)
            return "رد"

        agents["advisor"].handle = _spy_handle

        with patch(
            "app.agents.supervisor.agent.classify_intent",
            new=AsyncMock(return_value="advisor"),
        ):
            await supervisor.handle("سؤال", tenant_id, "sess-xyz")

        assert seen_tenant == [tenant_id]

    async def test_handle_returns_fallback_on_agent_error(self):
        from prompts import supervisor_ar

        supervisor, agents = _build_supervisor()
        agents["advisor"].handle = AsyncMock(side_effect=RuntimeError("agent down"))
        tenant_id = uuid.uuid4()

        with patch(
            "app.agents.supervisor.agent.classify_intent",
            new=AsyncMock(return_value="advisor"),
        ):
            result = await supervisor.handle("سؤال", tenant_id, "sess-err")

        assert result == supervisor_ar.FALLBACK_REPLY

    async def test_handle_uses_make_thread_id(self):
        """thread_id must be formed via make_thread_id — never inlined."""
        from app.infra.checkpointer import make_thread_id

        supervisor, _ = _build_supervisor()
        tenant_id = uuid.uuid4()
        session_id = "sess-thread"
        expected_thread = make_thread_id(tenant_id, session_id)

        captured_configs: list = []
        original_invoke = supervisor._graph.ainvoke

        async def _spy_invoke(state, config=None):
            captured_configs.append(config)
            return await original_invoke(state, config)

        supervisor._graph.ainvoke = _spy_invoke

        with patch(
            "app.agents.supervisor.agent.classify_intent",
            new=AsyncMock(return_value="advisor"),
        ):
            await supervisor.handle("سؤال", tenant_id, session_id)

        assert captured_configs[0]["configurable"]["thread_id"] == expected_thread


# ── supervisor compiles offline ────────────────────────────────────────────────


class TestSupervisorCompilation:
    def test_supervisor_compiles_without_checkpointer(self):
        """The supervisor must compile with checkpointer=None (offline CI)."""
        from app.agents.supervisor.agent import OwnerSupervisor

        supervisor = OwnerSupervisor(
            router=MagicMock(),
            order_agent=MagicMock(),
            inventory_agent=MagicMock(),
            finance_agent=MagicMock(),
            customer_agent=MagicMock(),
            advisor_agent=MagicMock(),
            checkpointer=None,
        )
        assert supervisor._graph is not None

    def test_all_five_intent_nodes_exist_in_graph(self):
        """Structural: graph must have one node per intent class."""
        from app.agents.supervisor.agent import _INTENT_NODES, OwnerSupervisor

        supervisor = OwnerSupervisor(
            router=MagicMock(),
            order_agent=MagicMock(),
            inventory_agent=MagicMock(),
            finance_agent=MagicMock(),
            customer_agent=MagicMock(),
            advisor_agent=MagicMock(),
            checkpointer=None,
        )
        graph_nodes = set(supervisor._graph.get_graph().nodes.keys())
        for intent in _INTENT_NODES:
            assert intent in graph_nodes, f"node '{intent}' missing from supervisor graph"
