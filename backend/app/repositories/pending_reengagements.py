"""Tenant-scoped repository for CustomerAgent HIL draft re-engagement messages."""

from uuid import UUID

from app.db.models import PendingReengagement
from app.repositories.base import TenantScopedRepository


class PendingReengagementRepository(TenantScopedRepository[PendingReengagement]):
    model = PendingReengagement

    async def find_draft_for_customer(
        self, tenant_id: UUID, customer_id: UUID
    ) -> PendingReengagement | None:
        """Return an existing draft for this customer (idempotency check), or None."""
        stmt = (
            self._require_tenant_scope(tenant_id)
            .where(PendingReengagement.customer_id == customer_id)
            .where(PendingReengagement.status == "draft")
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        tenant_id: UUID,
        *,
        customer_id: UUID,
        customer_name_ar: str | None,
        draft_message_ar: str,
        action_key: str,
    ) -> PendingReengagement:
        """Write a new draft re-engagement record, tenant-scoped. Flushes (no commit)."""
        record = PendingReengagement(
            customer_id=customer_id,
            customer_name_ar=customer_name_ar,
            draft_message_ar=draft_message_ar,
            action_key=action_key,
            status="draft",
        )
        return await self.add(tenant_id, record)
