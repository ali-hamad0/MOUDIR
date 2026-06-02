from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.guardrails import check_input, redact_pii
from app.domain.identity import ResolvedIdentity
from app.infra.logging import get_logger
from app.services.audit import AuditService
from prompts import order_ar

log = get_logger(__name__)


class OrderAgentLike(Protocol):
    """The slice of the OrderAgent the dispatcher needs.

    Typing against this Protocol (not the concrete agent) keeps the dispatcher
    decoupled and lets tests inject a fake.
    """

    async def handle(self, text: str, identity: ResolvedIdentity) -> str: ...


class MessageDispatcher:
    """Routes an inbound message by resolved role and wraps the customer path in
    the Layer 2 guardrails.

    Phase 2 implements the customer path; the owner path is a placeholder until
    Phase 7's supervisor. The OrderAgent is intentionally NOT reachable on the
    owner branch — tool allowlists are role-specific, enforced in code.

    Flow (customer):  input rail → agent → output PII redaction → reply.
    A tripped rail is audit-logged with tenant_id (GUARDRAILS.md) and the agent
    does NOT run.
    """

    def __init__(self, order_agent: OrderAgentLike, sessionmaker: async_sessionmaker) -> None:
        self._order_agent = order_agent
        self._sessionmaker = sessionmaker

    async def dispatch(self, text: str | None, identity: ResolvedIdentity) -> str:
        log.info(
            "message.dispatch",
            tenant_id=str(identity.tenant.id),
            role=identity.role,
        )
        if identity.role == "owner":
            # The agent and its tools are not even referenced here.
            return order_ar.OWNER_PLACEHOLDER

        # --- Input rail (Layer 2) ---
        rail = check_input(text)
        if not rail.allowed:
            await self._audit_rail(identity, rail.reason, rail.matched)
            log.info(
                "guardrail.input.blocked",
                tenant_id=str(identity.tenant.id),
                reason=rail.reason,
            )
            return order_ar.RAIL_REFUSAL

        reply = await self._order_agent.handle(text or "", identity)

        # --- Output rail (Layer 2): PII redaction on the outgoing text ---
        # "No hallucinated catalog item" is enforced upstream (parse drops
        # non-catalog ids; confirm re-validates) — the replies are template-based,
        # so the output rail here is the PII pass.
        return redact_pii(reply)

    async def _audit_rail(self, identity: ResolvedIdentity, reason: str | None, matched) -> None:
        async with self._sessionmaker() as session:
            await AuditService(session).record(
                tenant_id=identity.tenant.id,
                actor_id=identity.actor.id,
                action="rail.tripped",
                target=reason,
            )
            await session.commit()
