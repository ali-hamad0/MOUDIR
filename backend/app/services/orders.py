from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.order.schemas import ConfirmedOrder
from app.db.models import Order, OrderEvent, OrderItem
from app.domain.errors import ProductNotInCatalog, ProductUnavailable
from app.repositories.orders import OrderItemRepository, OrderRepository
from app.repositories.products import ProductRepository
from app.services.audit import AuditService


class OrderService:
    """The ONLY writer of orders. Tools call this; the agent never touches the
    session directly.

    Every line is re-validated against the live catalog here — tenant-scoped —
    even though parsing was already catalog-constrained. This is the final guard:
    a hallucinated or unavailable product is refused in code, not in a prompt
    (the Wall + "no hallucinated catalog item" enforced server-side).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_order(
        self,
        *,
        tenant_id: UUID,
        customer_id: UUID,
        confirmed: ConfirmedOrder,
        raw_message: str | None = None,
    ) -> Order:
        products = ProductRepository(self._session)

        order = Order(
            tenant_id=tenant_id,
            customer_id=customer_id,
            status="confirmed",
            fulfillment_type=confirmed.fulfillment_type,
            requested_time_text=confirmed.requested_time_text,
            raw_message=raw_message,
            note=confirmed.note,
        )
        order = await OrderRepository(self._session).add(tenant_id, order)

        items_repo = OrderItemRepository(self._session)
        total_lbp = 0
        total_usd = Decimal("0")
        for line in confirmed.items:
            # Re-load the product WITHIN this tenant's scope. A cross-tenant or
            # deleted id simply misses → ProductNotInCatalog, no row written.
            product = await products.get(tenant_id, line.product_id)
            if product is None:
                raise ProductNotInCatalog(line.product_id)
            if not product.is_available:
                raise ProductUnavailable(line.product_id, product.name_ar)

            unit_lbp = product.price_lbp
            unit_usd = product.price_usd
            line_total_lbp = (unit_lbp or 0) * line.quantity

            await items_repo.add(
                tenant_id,
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    # Snapshot name + unit price — a later catalog edit must not
                    # rewrite this past order.
                    name_ar_snapshot=product.name_ar,
                    quantity=line.quantity,
                    unit_price_lbp=unit_lbp,
                    unit_price_usd=unit_usd,
                    line_total_lbp=line_total_lbp,
                ),
            )
            total_lbp += line_total_lbp
            if unit_usd is not None:
                total_usd += unit_usd * line.quantity

        order.total_lbp = total_lbp
        order.total_usd = total_usd if total_usd > 0 else None

        self._session.add(OrderEvent(tenant_id=tenant_id, order_id=order.id, event="created"))
        await self._session.flush()

        await AuditService(self._session).record(
            tenant_id=tenant_id,
            actor_id=customer_id,
            action="order.confirmed",
            target=str(order.id),
        )
        await self._session.commit()
        return order
