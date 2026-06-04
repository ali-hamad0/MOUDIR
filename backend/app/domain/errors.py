"""Domain-level errors raised below the API layer.

These are NOT HTTPExceptions: the order flow runs inside the agent/tool layer
(driven by a webhook), so order failures surface as a Lebanese-Arabic reply, not
an HTTP status. The tool layer (Task 2.8) catches these and maps them to copy in
`prompts/order_ar.py`.
"""

from uuid import UUID


class OrderError(Exception):
    """Base for recoverable order-flow problems that become a customer reply."""


class ProductNotInCatalog(OrderError):
    """A requested line references a product id not in this tenant's catalog.

    A tenant-scoped lookup that misses (wrong tenant or deleted product) lands
    here — the order is never written for it.
    """

    def __init__(self, product_id: UUID) -> None:
        self.product_id = product_id
        super().__init__(f"product {product_id} not in catalog")


class ProductUnavailable(OrderError):
    """A requested product exists but is marked is_available=False."""

    def __init__(self, product_id: UUID, name_ar: str) -> None:
        self.product_id = product_id
        self.name_ar = name_ar
        super().__init__(f"product {product_id} ({name_ar}) is unavailable")


class InsufficientStock(OrderError):
    """A deduction asked for more units than the inventory row holds.

    Raised when InventoryRepository.deduct returns False — the guarded UPDATE
    (`quantity >= qty`) matched no row, so the completion transaction must roll
    back rather than write a negative quantity (the DB CHECK would reject it
    anyway). The order-completion service (Task 4.4) catches this and maps it to a
    409; nothing is partially deducted.
    """

    def __init__(self, product_id: UUID) -> None:
        self.product_id = product_id
        super().__init__(f"insufficient stock for product {product_id}")
