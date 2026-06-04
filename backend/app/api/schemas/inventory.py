"""Schemas for manual inventory + supplier management (Phase 4, Task 4.3).

The owner manages stock levels, reorder thresholds, and reorder targets from the
dashboard. tenant_id is NEVER part of any of these payloads — it comes from the
authenticated user's JWT (the Wall, constitution I). `is_low` is computed
server-side from the level vs. the threshold; it is read-only.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---- Inventory ----
class InventoryUpsert(BaseModel):
    """The mutable fields of an inventory row. The product is identified by the
    path parameter (and looked up tenant-scoped), never carried in the body."""

    quantity: int = Field(ge=0)
    reorder_threshold: int | None = Field(default=None, ge=0)
    reorder_quantity: int | None = Field(default=None, ge=0)
    supplier_id: UUID | None = None


class InventoryRead(BaseModel):
    """One inventory row joined to its catalog product for the dashboard view.

    The level/threshold/reorder/supplier come from the inventory row; the product
    names come from the single catalog table (joined, tenant-scoped on both
    sides). `is_low` is the low-stock flag — true when a threshold is set and the
    level is at/below it.
    """

    product_id: UUID
    name_ar: str
    name_en: str | None = None
    quantity: int
    reorder_threshold: int | None = None
    reorder_quantity: int | None = None
    supplier_id: UUID | None = None
    is_low: bool


class InventoryPage(BaseModel):
    """A paginated page of inventory rows. `total` is the full count for this
    tenant so the dashboard can paginate without loading everything."""

    items: list[InventoryRead]
    total: int
    limit: int
    offset: int


# ---- Suppliers ----
class SupplierUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    dispatch_type: str = Field(default="webhook", max_length=16)
    webhook_url: str | None = Field(default=None, max_length=1024)
    contact_email: str | None = Field(default=None, max_length=320)
    is_active: bool = True


class SupplierRead(SupplierUpsert):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
