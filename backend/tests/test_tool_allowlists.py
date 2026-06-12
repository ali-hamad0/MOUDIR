"""Task 7.8 — Tool allowlist structural enforcement (AD-7.8).

The graph topology IS the allowlist. Each specialist agent's compiled LangGraph
StateGraph contains only its own nodes — there is no edge from FinanceAgent to
queue_reengagement, no edge from CustomerAgent to check_stock, etc. These tests
enumerate the compiled graph's node names and assert the forbidden nodes are absent.

Two-layer enforcement (documented here, enforced in code):
  Layer 1 (dispatcher role gate): MessageDispatcher.dispatch() checks identity.role
    before touching the supervisor. A customer message goes to OrderAgent directly —
    the supervisor is never reached. See test_dispatcher.py for runtime proof.
  Layer 2 (per-agent graph topology): each compiled StateGraph has exactly its own
    nodes. No reachable path exists to another agent's tools.

No runtime LLM calls here. Agents are instantiated with stubs/None so _build_graph()
compiles the topology without any I/O.
"""

from contextlib import asynccontextmanager

from app.agents.advisor.agent import AdvisorAgent
from app.agents.customer.agent import CustomerAgent
from app.agents.finance.agent import FinanceAgent
from app.agents.inventory.agent import InventoryAgent
from app.agents.supervisor.agent import OwnerSupervisor


def _noop_sessionmaker():
    @asynccontextmanager
    async def _cm():
        yield object()

    return lambda: _cm()


def _graph_nodes(agent) -> set[str]:
    """Return user-defined node names from a compiled agent graph.

    LangGraph injects __start__ (and sometimes __end__) as internal sentinels;
    strip them so tests only see the developer-defined nodes.
    """
    return {n for n in agent._graph.nodes.keys() if not n.startswith("__")}


def _minimal_supervisor() -> OwnerSupervisor:
    class _StubAgent:
        async def handle(self, *args, **kwargs):
            return "stub"

    return OwnerSupervisor(
        router=None,
        order_agent=_StubAgent(),
        inventory_agent=_StubAgent(),
        finance_agent=_StubAgent(),
        customer_agent=_StubAgent(),
        advisor_agent=_StubAgent(),
        checkpointer=None,
    )


# ── FinanceAgent ─────────────────────────────────────────────────────────────

_FINANCE_ALLOWED = frozenset({"gather_revenue", "detect_anomalies", "compose_reply"})
_FINANCE_FORBIDDEN = frozenset(
    {
        "draft_purchase_order",  # InventoryAgent HIL
        "queue_reengagement",  # CustomerAgent HIL
        "queue_draft",  # CustomerAgent
        "send_message",  # any send path
        "check_stock",  # InventoryAgent
        "forecast",  # InventoryAgent
        "draft",  # InventoryAgent
        "assess_risks",  # CustomerAgent
        "draft_message",  # CustomerAgent
        "gather_snapshot",  # AdvisorAgent
        "gather_demand",  # AdvisorAgent
        "gather_churn",  # AdvisorAgent
        "gather_anomaly",  # AdvisorAgent
        "compose_briefing",  # AdvisorAgent
    }
)


def test_finance_agent_has_exactly_its_own_nodes():
    agent = FinanceAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent) == _FINANCE_ALLOWED


def test_finance_agent_has_no_forbidden_nodes():
    agent = FinanceAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent).isdisjoint(_FINANCE_FORBIDDEN)


def test_finance_agent_has_no_draft_purchase_order_node():
    agent = FinanceAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert "draft_purchase_order" not in _graph_nodes(agent)


def test_finance_agent_has_no_queue_reengagement_node():
    agent = FinanceAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert "queue_reengagement" not in _graph_nodes(agent)


# ── CustomerAgent ─────────────────────────────────────────────────────────────

_CUSTOMER_ALLOWED = frozenset({"assess_risks", "draft_message", "queue_draft", "compose_reply"})
_CUSTOMER_FORBIDDEN = frozenset(
    {
        "draft_purchase_order",  # InventoryAgent HIL
        "check_stock",  # InventoryAgent
        "forecast",  # InventoryAgent
        "draft",  # InventoryAgent
        "gather_revenue",  # FinanceAgent
        "detect_anomalies",  # FinanceAgent
        "gather_snapshot",  # AdvisorAgent
        "gather_demand",  # AdvisorAgent
        "gather_churn",  # AdvisorAgent
        "gather_anomaly",  # AdvisorAgent
        "compose_briefing",  # AdvisorAgent
        "send_message",  # any send path
    }
)


def test_customer_agent_has_exactly_its_own_nodes():
    agent = CustomerAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent) == _CUSTOMER_ALLOWED


def test_customer_agent_has_no_forbidden_nodes():
    agent = CustomerAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent).isdisjoint(_CUSTOMER_FORBIDDEN)


def test_customer_agent_has_no_draft_purchase_order_node():
    agent = CustomerAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert "draft_purchase_order" not in _graph_nodes(agent)


def test_customer_agent_has_no_check_stock_node():
    agent = CustomerAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert "check_stock" not in _graph_nodes(agent)


# ── AdvisorAgent ─────────────────────────────────────────────────────────────

_ADVISOR_ALLOWED = frozenset(
    {
        "gather_snapshot",
        "gather_demand",
        "gather_churn",
        "gather_anomaly",
        "compose_briefing",
    }
)
_ADVISOR_FORBIDDEN_HIL = frozenset(
    {
        "queue_reengagement",
        "queue_draft",
        "draft_purchase_order",
        "draft",
        "send_message",
    }
)


def test_advisor_agent_has_exactly_its_own_nodes():
    agent = AdvisorAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent) == _ADVISOR_ALLOWED


def test_advisor_agent_has_no_hil_action_nodes():
    agent = AdvisorAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent).isdisjoint(_ADVISOR_FORBIDDEN_HIL)


def test_advisor_agent_has_no_queue_reengagement_node():
    agent = AdvisorAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert "queue_reengagement" not in _graph_nodes(agent)


def test_advisor_agent_has_no_draft_purchase_order_node():
    agent = AdvisorAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert "draft_purchase_order" not in _graph_nodes(agent)


# ── InventoryAgent ────────────────────────────────────────────────────────────

_INVENTORY_ALLOWED = frozenset({"check_stock", "forecast", "draft"})
_INVENTORY_FORBIDDEN = frozenset(
    {
        "queue_reengagement",
        "send_message",
        "gather_revenue",
        "detect_anomalies",
        "assess_risks",
        "draft_message",
        "queue_draft",
        "gather_snapshot",
        "gather_demand",
        "gather_churn",
        "gather_anomaly",
        "compose_briefing",
    }
)


def test_inventory_agent_has_exactly_its_own_nodes():
    agent = InventoryAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent) == _INVENTORY_ALLOWED


def test_inventory_agent_has_no_hil_send_path():
    agent = InventoryAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    nodes = _graph_nodes(agent)
    assert "send_message" not in nodes
    assert "queue_reengagement" not in nodes


def test_inventory_agent_has_no_forbidden_nodes():
    agent = InventoryAgent(router=None, settings=None, sessionmaker=_noop_sessionmaker())
    assert _graph_nodes(agent).isdisjoint(_INVENTORY_FORBIDDEN)


# ── OwnerSupervisor ────────────────────────────────────────────────────────────

_SUPERVISOR_ALLOWED = frozenset(
    {
        "route",
        "order",
        "inventory",
        "finance",
        "customer",
        "advisor",
        # Phase 10: the confirmed/cancelled stock-edit steps. Only reachable from
        # route when a pending_adjustment sits in state — owner path only.
        "apply_adjustment",
        "cancel_adjustment",
        "respond",
    }
)


def test_supervisor_has_exactly_its_own_nodes():
    sup = _minimal_supervisor()
    assert _graph_nodes(sup) == _SUPERVISOR_ALLOWED


def test_supervisor_has_all_five_intent_nodes():
    sup = _minimal_supervisor()
    nodes = _graph_nodes(sup)
    for intent in ("order", "inventory", "finance", "customer", "advisor"):
        assert intent in nodes, f"supervisor is missing node for intent '{intent}'"


def test_supervisor_has_route_and_respond_orchestration_nodes():
    sup = _minimal_supervisor()
    nodes = _graph_nodes(sup)
    assert "route" in nodes
    assert "respond" in nodes


# ── Dispatcher layer-1 gate (role check) ─────────────────────────────────────


async def test_dispatcher_customer_path_never_calls_supervisor():
    """Layer 1 gate: a customer-role dispatch must not touch the supervisor.

    Injects a forbidden supervisor whose handle() raises if called, then
    dispatches a customer message and asserts no exception is raised.
    """
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from app.domain.identity import ResolvedIdentity
    from app.services.dispatcher import MessageDispatcher

    class _ForbiddenSupervisor:
        async def handle(self, *args, **kwargs):
            raise AssertionError("supervisor.handle must not be called on the customer path")

    class _FakeTenant:
        id = uuid4()

    class _FakeActor:
        id = uuid4()

    class _SpyAgent:
        async def handle(self, text, identity):
            return "AGENT_REPLY"

    @asynccontextmanager
    async def _noop_session():
        yield object()

    identity = ResolvedIdentity(tenant=_FakeTenant(), role="customer", actor=_FakeActor())
    reply = await MessageDispatcher(
        _SpyAgent(), lambda: _noop_session(), supervisor=_ForbiddenSupervisor()
    ).dispatch("بدي طلب", identity)
    assert reply == "AGENT_REPLY"


async def test_dispatcher_owner_path_never_calls_order_agent():
    """Layer 1 gate: an owner-role dispatch must not touch the OrderAgent.

    Injects a forbidden order agent whose handle() raises if called, then
    dispatches an owner message through the supervisor and asserts no exception.
    """
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from app.domain.identity import ResolvedIdentity
    from app.services.dispatcher import MessageDispatcher

    class _FakeOwnerSupervisor:
        async def handle(self, message, tenant_id, session_id):
            return "SUPERVISOR_REPLY"

    class _FakeTenant:
        id = uuid4()

    class _FakeActor:
        id = uuid4()

    class _ForbiddenOrderAgent:
        async def handle(self, *args, **kwargs):
            raise AssertionError("OrderAgent.handle must not be called on the owner path")

    @asynccontextmanager
    async def _noop_session():
        yield object()

    identity = ResolvedIdentity(tenant=_FakeTenant(), role="owner", actor=_FakeActor())
    reply = await MessageDispatcher(
        _ForbiddenOrderAgent(), lambda: _noop_session(), supervisor=_FakeOwnerSupervisor()
    ).dispatch("كيف مبيعاتي؟", identity)
    assert reply == "SUPERVISOR_REPLY"
