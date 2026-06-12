from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SubscriptionPayment


class SubscriptionPaymentRepository:
    """Subscription payment history. Every method is tenant-scoped (The Wall)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant_id: UUID, payment: SubscriptionPayment) -> SubscriptionPayment:
        payment.tenant_id = tenant_id
        self._session.add(payment)
        await self._session.flush()
        return payment

    async def list_for_tenant(self, tenant_id: UUID) -> Sequence[SubscriptionPayment]:
        result = await self._session.execute(
            select(SubscriptionPayment)
            .where(SubscriptionPayment.tenant_id == tenant_id)
            .order_by(SubscriptionPayment.created_at.desc())
        )
        return result.scalars().all()
