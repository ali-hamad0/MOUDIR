"""Read schemas for the dashboard order feed (Phase 3, Task 3.2).

These are READ models only — the order-writing contracts live in
`app/agents/order/schemas.py`. Money fields are the LBP/USD snapshots taken at
confirm time (Phase 2); the dashboard shows LBP primary, USD secondary and does
no live conversion.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    name_ar_snapshot: str
    quantity: int
    unit_price_lbp: int | None = None
    unit_price_usd: Decimal | None = None
    line_total_lbp: int | None = None


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    fulfillment_type: str
    # The customer's raw Arabic time phrase ("بكرا الصبح"), kept verbatim.
    requested_time_text: str | None = None
    total_lbp: int | None = None
    total_usd: Decimal | None = None
    created_at: datetime
    # Customer display, joined in — phone is intentionally NOT exposed in the feed.
    customer_id: UUID
    customer_display_name: str | None = None
    items: list[OrderItemRead] = []


class OrdersPage(BaseModel):
    """A paginated page of orders. `total` is the full count for this tenant's
    query so the dashboard can render pagination without loading everything."""

    items: list[OrderRead]
    total: int
    limit: int
    offset: int
