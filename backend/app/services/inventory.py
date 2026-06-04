from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.inventory import InventoryUpsert, SupplierUpsert
from app.db.models import Inventory, Product, Supplier
from app.repositories.inventory import InventoryRepository
from app.repositories.products import ProductRepository
from app.repositories.suppliers import SupplierRepository
from app.services.audit import AuditService
from prompts import inventory_ar


def is_low(quantity: int, reorder_threshold: int | None) -> bool:
    """The low-stock flag: a threshold must be set AND the level at/below it.

    A null threshold means the owner has not opted this product into reorder
    tracking, so it is never flagged low (same rule as InventoryRepository.
    list_low_stock — kept consistent on purpose)."""
    return reorder_threshold is not None and quantity <= reorder_threshold


class InventoryService:
    """Manual inventory + supplier management, all tenant-scoped through
    repositories (the Wall). Mirrors ProfileService: the tenant_id is the
    caller's JWT scope, every write is audited, and the transaction commits once.

    The upsert verifies the product belongs to the caller's tenant via a scoped
    lookup — a cross-tenant (or deleted) product id simply misses and becomes a
    404, never a write against another tenant's row.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._inventory = InventoryRepository(session)
        self._suppliers = SupplierRepository(session)
        self._products = ProductRepository(session)
        self._audit_service = AuditService(session)

    async def _audit(self, tenant_id: UUID, actor_id: UUID, action: str, target: str) -> None:
        await self._audit_service.record(
            tenant_id=tenant_id, actor_id=actor_id, action=action, target=target
        )

    # ---- Inventory ----
    async def list_inventory(
        self, *, tenant_id: UUID, limit: int, offset: int
    ) -> tuple[int, Sequence[Row[tuple[Inventory, Product]]]]:
        """One page of inventory joined to the catalog plus the total count, both
        in the caller's tenant scope."""
        total = await self._inventory.count(tenant_id)
        rows = await self._inventory.list_with_product(tenant_id, limit=limit, offset=offset)
        return total, rows

    async def upsert_inventory(
        self, *, tenant_id: UUID, actor_id: UUID, product_id: UUID, data: InventoryUpsert
    ) -> tuple[Inventory, Product]:
        """Set the stock level + reorder config for a product the tenant owns.

        404 if the product is not this tenant's (scoped lookup). Creates the
        inventory row on first set, updates it after — one row per
        (tenant_id, product_id). A manual level change is audited as
        `inventory.adjusted`. Returns the inventory row with its product so the
        caller can render the joined read model without a second lookup.
        """
        product = await self._products.get(tenant_id, product_id)
        if product is None:
            # A cross-tenant id misses the scoped lookup -> 404, not 403.
            raise HTTPException(status.HTTP_404_NOT_FOUND, inventory_ar.PRODUCT_NOT_FOUND)

        # A supplier_id, if given, must also be this tenant's — never reference
        # another tenant's supplier across the Wall.
        if data.supplier_id is not None:
            supplier = await self._suppliers.get(tenant_id, data.supplier_id)
            if supplier is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, inventory_ar.SUPPLIER_NOT_FOUND)

        row = await self._inventory.get_by_product(tenant_id, product_id)
        if row is None:
            row = await self._inventory.add(tenant_id, Inventory(product_id=product_id))
        row.quantity = data.quantity
        row.reorder_threshold = data.reorder_threshold
        row.reorder_quantity = data.reorder_quantity
        row.supplier_id = data.supplier_id
        await self._session.flush()
        await self._audit(tenant_id, actor_id, "inventory.adjusted", str(product_id))
        await self._session.commit()
        return row, product

    # ---- Suppliers ----
    async def list_suppliers(self, *, tenant_id: UUID) -> Sequence[Supplier]:
        return await self._suppliers.list(tenant_id)

    async def create_supplier(
        self, *, tenant_id: UUID, actor_id: UUID, data: SupplierUpsert
    ) -> Supplier:
        supplier = await self._suppliers.add(tenant_id, Supplier(**data.model_dump()))
        await self._audit(tenant_id, actor_id, "supplier.created", str(supplier.id))
        await self._session.commit()
        return supplier

    async def update_supplier(
        self, *, tenant_id: UUID, actor_id: UUID, supplier_id: UUID, data: SupplierUpsert
    ) -> Supplier:
        supplier = await self._suppliers.get(tenant_id, supplier_id)
        if supplier is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, inventory_ar.SUPPLIER_NOT_FOUND)
        for field, value in data.model_dump().items():
            setattr(supplier, field, value)
        await self._session.flush()
        await self._audit(tenant_id, actor_id, "supplier.updated", str(supplier_id))
        await self._session.commit()
        return supplier
