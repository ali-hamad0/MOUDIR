from typing import Protocol

from app.domain.identity import ResolvedIdentity
from app.infra.logging import get_logger
from prompts import order_ar

log = get_logger(__name__)


class OrderAgentLike(Protocol):
    """The slice of the OrderAgent the dispatcher needs.

    Typing against this Protocol (not the concrete agent, which lands in Task 2.9)
    keeps the dispatcher decoupled and lets tests inject a fake.
    """

    async def handle(self, text: str, identity: ResolvedIdentity) -> str: ...


class MessageDispatcher:
    """Routes an inbound message by resolved role.

    Phase 2 implements the customer path; the owner path is a placeholder until
    Phase 7's supervisor. The OrderAgent is intentionally NOT reachable on the
    owner branch — tool allowlists are role-specific, enforced in code, not in a
    prompt (constitution / ROADMAP pitfall).
    """

    def __init__(self, order_agent: OrderAgentLike) -> None:
        self._order_agent = order_agent

    async def dispatch(self, text: str | None, identity: ResolvedIdentity) -> str:
        log.info(
            "message.dispatch",
            tenant_id=str(identity.tenant.id),
            role=identity.role,
        )
        if identity.role == "owner":
            # The agent and its tools are not even referenced here.
            return order_ar.OWNER_PLACEHOLDER
        return await self._order_agent.handle(text or "", identity)
