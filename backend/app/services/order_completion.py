from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderEvent
from app.domain.errors import InsufficientStock
from app.infra.logging import get_logger
from app.repositories.inventory import InventoryRepository
from app.repositories.orders import OrderItemRepository, OrderRepository
from app.repositories.purchase_orders import PurchaseOrderRepository
from app.services.audit import AuditService

if TYPE_CHECKING:
    from app.agents.inventory.agent import InventoryAgent

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

    def __init__(
        self, session: AsyncSession, inventory_agent: "InventoryAgent | None" = None
    ) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._items = OrderItemRepository(session)
        self._inventory = InventoryRepository(session)
        self._purchase_orders = PurchaseOrderRepository(session)
        self._audit = AuditService(session)
        # The InventoryAgent that drafts reorder POs (Task 4.9). Injected from
        # app.state (built once in lifespan); None in unit tests that exercise
        # deduction alone, where the post-commit drafting hook is simply skipped.
        self._inventory_agent = inventory_agent

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

        # Deduct every tracked line, then flip status + audit — all atomic.
        # items_for_orders is already tenant-scoped (the Wall) and batched.
        #
        # The deduction loop runs inside a SAVEPOINT (begin_nested). A short line
        # raises InsufficientStock, which unwinds the savepoint and undoes EVERY
        # prior deduct in this completion — no partial deduction ever lands. A
        # savepoint (not a full session.rollback) is deliberate: it discards only
        # this completion's writes, never anything the caller already did in the
        # same session. The line's status flip + audit + commit happen after the
        # savepoint succeeds.
        items = await self._items_for_order(tenant_id, order_id)
        savepoint = await self._session.begin_nested()
        try:
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
                    # Short line: the guarded UPDATE matched nothing. Unwind the
                    # savepoint so every prior deduct in this completion is undone
                    # — no partial deduction lands — then surface the error. The
                    # API maps InsufficientStock to a 409.
                    raise InsufficientStock(item.product_id)
        except InsufficientStock:
            await savepoint.rollback()
            raise
        await savepoint.commit()

        order.status = "completed"
        self._session.add(OrderEvent(tenant_id=tenant_id, order_id=order_id, event="completed"))
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="order.completed",
            target=str(order_id),
        )
        await self._session.commit()

        # ── Low-stock reorder PO drafting (Task 4.9) ────────────────────────
        # After the completion COMMIT, draft a reorder PO for any product the
        # deduction pushed at/below its threshold. This runs after commit on
        # purpose: the order is already `completed`, so a drafting hiccup must
        # never roll back a real fulfillment (a missed draft is recoverable — the
        # threshold trips again on the next completion).
        await self._draft_low_stock_reorders(tenant_id)

        return order

    async def _draft_low_stock_reorders(self, tenant_id: UUID) -> None:
        """For each product now at/below its reorder threshold, draft a reorder PO
        — unless one is already open (idempotent, no approval spam).

        Reads the freshly committed levels via `list_low_stock`, then for each
        candidate that has no open (draft/approved-unsent) PO asks the
        InventoryAgent to draft one. The agent opens and commits its OWN session,
        so a draft is its own unit of work, decoupled from the completion txn.

        This is best-effort: any failure (a low-stock read, an agent draft) is
        logged and swallowed — the order completion already succeeded and a missed
        draft is recoverable. No exception here ever propagates to the caller.
        """
        if self._inventory_agent is None:
            return
        try:
            low = await self._inventory.list_low_stock(tenant_id)
        except Exception:
            log.exception("order_completion.low_stock_read_failed", tenant_id=str(tenant_id))
            return

        for row in low:
            try:
                if await self._purchase_orders.has_open_po_for_product(tenant_id, row.product_id):
                    # Already an open PO for this product — don't re-draft (the
                    # owner would get a duplicate approval). Idempotent on open PO.
                    continue
                po_id = await self._inventory_agent.draft_for_low_stock(tenant_id, row.product_id)
                if po_id is not None:
                    log.info(
                        "order_completion.po_drafted",
                        tenant_id=str(tenant_id),
                        product_id=str(row.product_id),
                        purchase_order_id=str(po_id),
                    )
            except Exception:
                # One product's drafting failing must not stop the others, and
                # never surfaces to the (already-successful) completion call.
                log.exception(
                    "order_completion.draft_failed",
                    tenant_id=str(tenant_id),
                    product_id=str(row.product_id),
                )

    async def _items_for_order(self, tenant_id: UUID, order_id: UUID):
        """Every line of one order, tenant-scoped. Reuses the batched
        items_for_orders so the scope (the Wall) stays in one place."""
        return await self._orders.items_for_orders(tenant_id, [order_id])
