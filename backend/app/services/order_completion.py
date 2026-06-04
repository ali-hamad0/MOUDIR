from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderEvent
from app.domain.errors import InsufficientStock
from app.infra.logging import get_logger
from app.repositories.inventory import InventoryRepository
from app.repositories.orders import OrderItemRepository, OrderRepository
from app.services.audit import AuditService

log = get_logger(__name__)


class OrderNotFound(Exception):
    """The order id is not this tenant's (scoped lookup missed) or doesn't exist.

    Surfaces as a 404 in the API layer. A cross-tenant id lands here exactly the
    same way a deleted id does — the Wall never reveals that another tenant's
    order exists.
    """

    def __init__(self, order_id: UUID) -> None:
        self.order_id = order_id
        super().__init__(f"order {order_id} not found")


class OrderNotCompletable(Exception):
    """The order is not in `confirmed` status, so it cannot be completed.

    Completing an already-completed order lands here (idempotency is a 409, not a
    silent no-op — the documented Task 4.4 choice). Surfaces as a 409.
    """

    def __init__(self, order_id: UUID, status: str) -> None:
        self.order_id = order_id
        self.status = status
        super().__init__(f"order {order_id} is {status}, not confirmed")


class OrderCompletionService:
    """Moves a confirmed order to `completed` and deducts every tracked line from
    inventory in ONE transaction.

    Deduction happens on completion, not confirmation: a confirmed order can be
    cancelled before fulfillment, so stock must only move when the shop actually
    fulfills it. This service is the writer of the `confirmed -> completed`
    transition (OrderService writes creation; this writes completion).

    Atomicity rule (no partial deduction): every line's deduct runs inside the
    one transaction, and any short line raises InsufficientStock — the whole
    completion rolls back and the order stays `confirmed`. A product with no
    inventory row is *untracked* (the owner may not track every SKU yet): that
    line is skipped and logged, never blocking fulfillment.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._items = OrderItemRepository(session)
        self._inventory = InventoryRepository(session)
        self._audit = AuditService(session)

    async def complete(self, *, tenant_id: UUID, order_id: UUID, actor_id: UUID) -> Order:
        """Complete a confirmed order, deducting stock atomically.

        Raises OrderNotFound (404) if the order isn't this tenant's,
        OrderNotCompletable (409) if it isn't `confirmed`, and InsufficientStock
        (409) if any tracked line is short — the latter rolls the whole thing back.
        """
        # Tenant-scoped load: a cross-tenant or deleted id simply misses.
        order = await self._orders.get(tenant_id, order_id)
        if order is None:
            raise OrderNotFound(order_id)
        if order.status != "confirmed":
            raise OrderNotCompletable(order_id, order.status)

        # One transaction: deduct every tracked line, then flip status + audit.
        # items_for_orders is already tenant-scoped (the Wall) and batched.
        items = await self._items_for_order(tenant_id, order_id)
        for item in items:
            row = await self._inventory.get_by_product(tenant_id, item.product_id)
            if row is None:
                # Untracked product (no inventory row) -> skip, don't block.
                # The owner may not track this SKU yet; fulfillment proceeds.
                log.info(
                    "order_completion.untracked_line",
                    tenant_id=str(tenant_id),
                    order_id=str(order_id),
                    product_id=str(item.product_id),
                )
                continue
            deducted = await self._inventory.deduct(tenant_id, item.product_id, item.quantity)
            if not deducted:
                # Short line: the guarded UPDATE matched nothing. Raising here
                # rolls back every prior deduct in this transaction — no partial
                # deduction ever lands. The API maps this to a 409.
                raise InsufficientStock(item.product_id)

        order.status = "completed"
        self._session.add(OrderEvent(tenant_id=tenant_id, order_id=order_id, event="completed"))
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="order.completed",
            target=str(order_id),
        )
        await self._session.commit()

        # ── HOOK (Task 4.9): low-stock reorder PO drafting ──────────────────
        # After this commit, any product now at/below its reorder threshold with
        # no open PO should get a draft PO from the InventoryAgent. This runs
        # AFTER commit on purpose — a draft hiccup must never roll back a real
        # fulfillment, and the order is already `completed` at this point. Task
        # 4.9 wires `inventory_agent.draft_for_low_stock(...)` here (idempotent on
        # an existing open PO). Intentionally a no-op until then.

        return order

    async def _items_for_order(self, tenant_id: UUID, order_id: UUID):
        """Every line of one order, tenant-scoped. Reuses the batched
        items_for_orders so the scope (the Wall) stays in one place."""
        return await self._orders.items_for_orders(tenant_id, [order_id])
