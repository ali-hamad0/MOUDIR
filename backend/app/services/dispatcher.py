from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.guardrails import check_input, redact_pii
from app.db.models import Customer
from app.domain.identity import ResolvedIdentity
from app.infra.logging import get_logger
from app.infra.session import make_session_id
from app.services.audit import AuditService
from app.services.customer_enrichment import CustomerEnrichmentService
from prompts import order_ar

log = get_logger(__name__)


class OrderAgentLike(Protocol):
    """The slice of the OrderAgent the dispatcher needs.

    Typing against this Protocol (not the concrete agent) keeps the dispatcher
    decoupled and lets tests inject a fake.
    """

    async def handle(self, text: str, identity: ResolvedIdentity) -> str: ...


class OwnerSupervisorLike(Protocol):
    """The slice of the OwnerSupervisor the dispatcher needs (Task 7.7).

    The dispatcher types against this Protocol so tests can inject any async
    callable with the right signature — not the concrete supervisor.
    """

    async def handle(self, message: str, tenant_id: UUID, session_id: str) -> str: ...


class MessageDispatcher:
    """Routes an inbound message by resolved role and wraps the customer path in
    the Layer 2 guardrails.

    The customer path goes to OrderAgent (unchanged from Phase 2).
    The owner path goes to OwnerSupervisor (Task 7.7); falls back to the
    OWNER_PLACEHOLDER when no supervisor is wired (pre-Phase 7 behaviour,
    preserved so older tests and cold starts stay valid).

    Flow (customer):  input rail → agent → output PII redaction → reply.
    A tripped rail is audit-logged with tenant_id (GUARDRAILS.md) and the agent
    does NOT run.
    """

    def __init__(
        self,
        order_agent: OrderAgentLike,
        sessionmaker: async_sessionmaker,
        supervisor: OwnerSupervisorLike | None = None,
    ) -> None:
        self._order_agent = order_agent
        self._sessionmaker = sessionmaker
        self._supervisor = supervisor

    async def dispatch(self, text: str | None, identity: ResolvedIdentity) -> str:
        log.info(
            "message.dispatch",
            tenant_id=str(identity.tenant.id),
            role=identity.role,
        )
        if identity.role == "owner":
            if self._supervisor is None:
                return order_ar.OWNER_PLACEHOLDER
            session_id = make_session_id(identity)
            return await self._supervisor.handle(text or "", identity.tenant.id, session_id)

        # --- Input rail (Layer 2) ---
        rail = check_input(text, role=identity.role)
        if not rail.allowed:
            await self._audit_rail(identity, rail.reason, rail.matched)
            log.info(
                "guardrail.input.blocked",
                tenant_id=str(identity.tenant.id),
                reason=rail.reason,
            )
            return order_ar.RAIL_REFUSAL

        # --- Customer name enrichment (best-effort; never blocks the order) ---
        await self._maybe_enrich_customer(identity, text)

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

    async def _maybe_enrich_customer(self, identity: ResolvedIdentity, text: str | None) -> None:
        # Only customers carry a display_name to enrich; never let a failure here
        # break the order flow.
        if not isinstance(identity.actor, Customer):
            return
        try:
            async with self._sessionmaker() as session:
                await CustomerEnrichmentService(session).enrich_from_message(
                    tenant_id=identity.tenant.id, customer=identity.actor, text=text
                )
        except Exception as e:  # enrichment is best-effort, not on the critical path
            log.warning(
                "customer.enrich.failed",
                tenant_id=str(identity.tenant.id),
                error=str(e),
            )
