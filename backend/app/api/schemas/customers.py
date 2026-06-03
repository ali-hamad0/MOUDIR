"""Read schemas for the dashboard customer list (Phase 3, Task 3.3).

Read-only. Totals are summed from the order snapshots; the dashboard shows
total spent in LBP primary, USD secondary and does no live conversion.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class CustomerRead(BaseModel):
    id: UUID
    display_name: str | None = None
    # The owner sees their own tenant's customer phone; it is redacted in LOGS
    # (constitution III), not in this owner-facing view of their own data.
    phone_number: str
    first_seen_at: datetime
    order_count: int
    total_spent_lbp: int
    total_spent_usd: Decimal | None = None
    last_order_at: datetime | None = None


class CustomersPage(BaseModel):
    items: list[CustomerRead]
    total: int
    limit: int
    offset: int
