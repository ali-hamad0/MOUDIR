from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog
from app.infra.logging import get_logger

log = get_logger(__name__)


class AuditService:
    """The one place audit events are written. Every auth, privilege, and
    profile change routes through here so each event is shaped consistently and
    always carries tenant_id (constitution III).

    record() flushes but does NOT commit — it joins the caller's transaction so
    the audit row lands atomically with the change it describes.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        tenant_id: UUID,
        action: str,
        actor_id: UUID | None = None,
        target: str | None = None,
    ) -> None:
        entry = AuditLog(tenant_id=tenant_id, actor_id=actor_id, action=action, target=target)
        self._session.add(entry)
        await self._session.flush()
        # Structured log mirrors the audit row; tenant_id always present.
        log.info(
            "audit",
            tenant_id=str(tenant_id),
            action=action,
            actor_id=str(actor_id) if actor_id else None,
            target=target,
        )
