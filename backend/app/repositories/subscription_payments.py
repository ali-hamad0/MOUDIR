from collections.abc import Sequence
from uuid import UUID

from app.db.models import SubscriptionPayment
from app.repositories.base import TenantScopedRepository


class SubscriptionPaymentRepository(TenantScopedRepository[SubscriptionPayment]):
    """Subscription payment history. Inherits the Wall from the base."""

    model = SubscriptionPayment

    async def list_for_tenant(self, tenant_id: UUID) -> Sequence[SubscriptionPayment]:
        stmt = self._require_tenant_scope(tenant_id).order_by(SubscriptionPayment.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()
