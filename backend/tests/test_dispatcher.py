"""Task 2.7 — message dispatcher role routing.

Proves the owner path returns the placeholder WITHOUT invoking the OrderAgent
(tool allowlists are role-specific, in code), and the customer path routes to the
agent. A spy fake stands in for the real OrderAgent (Task 2.9).
"""

from dataclasses import dataclass

from app.domain.identity import ResolvedIdentity
from app.services.dispatcher import MessageDispatcher
from prompts import order_ar


@dataclass
class _FakeTenant:
    id: str = "11111111-1111-1111-1111-111111111111"


class _SpyAgent:
    def __init__(self) -> None:
        self.called_with: tuple[str, ResolvedIdentity] | None = None

    async def handle(self, text: str, identity: ResolvedIdentity) -> str:
        self.called_with = (text, identity)
        return "AGENT_REPLY"


def _identity(role: str) -> ResolvedIdentity:
    # actor is irrelevant to routing; the dispatcher only reads tenant + role.
    return ResolvedIdentity(tenant=_FakeTenant(), role=role, actor=object())


async def test_owner_routes_to_placeholder_without_calling_agent():
    spy = _SpyAgent()
    reply = await MessageDispatcher(spy).dispatch("بدي ٥ كعكات", _identity("owner"))
    assert reply == order_ar.OWNER_PLACEHOLDER
    assert spy.called_with is None  # the agent was never invoked on the owner branch


async def test_customer_routes_to_agent():
    spy = _SpyAgent()
    reply = await MessageDispatcher(spy).dispatch("بدي ٥ كعكات", _identity("customer"))
    assert reply == "AGENT_REPLY"
    assert spy.called_with is not None
    assert spy.called_with[0] == "بدي ٥ كعكات"


async def test_none_text_becomes_empty_string_for_agent():
    spy = _SpyAgent()
    await MessageDispatcher(spy).dispatch(None, _identity("customer"))
    assert spy.called_with[0] == ""
