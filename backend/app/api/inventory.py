from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.inventory import (
    InventoryPage,
    InventoryRead,
    InventoryUpsert,
    SupplierRead,
    SupplierUpsert,
)
from app.db.models import Inventory, Product, User
from app.db.session import get_db_session
from app.services.inventory import InventoryService, is_low

router = APIRouter(tags=["inventory"])

# Every route below is tenant-scoped: tenant_id comes from the authenticated
# user's JWT, never from the request body or path. The Wall holds here.
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


def _to_read(product: Product, inventory: Inventory | None) -> InventoryRead:
    """Map a (product, inventory | None) pair to the read model.

    inventory is None for products that have never had their stock set — they
    appear in the list with quantity 0 and no thresholds so the owner can edit
    them without first having to do anything special.
    """
    qty = inventory.quantity if inventory is not None else 0
    threshold = inventory.reorder_threshold if inventory is not None else None
    reorder_qty = inventory.reorder_quantity if inventory is not None else None
    supplier_id = inventory.supplier_id if inventory is not None else None
    return InventoryRead(
        product_id=product.id,
        name_ar=product.name_ar,
        name_en=product.name_en,
        quantity=qty,
        reorder_threshold=threshold,
        reorder_quantity=reorder_qty,
        supplier_id=supplier_id,
        is_low=is_low(qty, threshold),
    )


@router.get("/inventory", response_model=InventoryPage)
async def list_inventory(
    user: CurrentUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InventoryPage:
    """This tenant's inventory joined to the catalog, paginated, low-stock
    flagged. Scoped to the JWT tenant only — never shows another tenant's
    products."""
    total, rows = await InventoryService(db).list_inventory(
        tenant_id=user.tenant_id, limit=limit, offset=offset
    )
    items = [_to_read(product, inventory) for product, inventory in rows]
    return InventoryPage(items=items, total=total, limit=limit, offset=offset)


@router.put("/inventory/{product_id}", response_model=InventoryRead)
async def upsert_inventory(
    product_id: UUID, payload: InventoryUpsert, user: CurrentUser, db: Db
) -> InventoryRead:
    """Set the level + reorder config for a product this tenant owns (404 if the
    product isn't this tenant's). Creates the inventory row on first set; the
    manual change is audited."""
    row, product = await InventoryService(db).upsert_inventory(
        tenant_id=user.tenant_id, actor_id=user.id, product_id=product_id, data=payload
    )
    return _to_read(product, row)


@router.get("/suppliers", response_model=list[SupplierRead])
async def list_suppliers(user: CurrentUser, db: Db) -> list[SupplierRead]:
    """This tenant's suppliers."""
    rows = await InventoryService(db).list_suppliers(tenant_id=user.tenant_id)
    return [SupplierRead.model_validate(r) for r in rows]


@router.post("/suppliers", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
async def create_supplier(payload: SupplierUpsert, user: CurrentUser, db: Db) -> SupplierRead:
    supplier = await InventoryService(db).create_supplier(
        tenant_id=user.tenant_id, actor_id=user.id, data=payload
    )
    return SupplierRead.model_validate(supplier)


@router.put("/suppliers/{supplier_id}", response_model=SupplierRead)
async def update_supplier(
    supplier_id: UUID, payload: SupplierUpsert, user: CurrentUser, db: Db
) -> SupplierRead:
    supplier = await InventoryService(db).update_supplier(
        tenant_id=user.tenant_id, actor_id=user.id, supplier_id=supplier_id, data=payload
    )
    return SupplierRead.model_validate(supplier)
