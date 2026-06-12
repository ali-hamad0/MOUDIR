from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.profile import (
    DayHoursResponse,
    OperatingHoursReplace,
    PoliciesUpsert,
    PolicyResponse,
    ProductResponse,
    ProductWrite,
    ProfileResponse,
    ProfileUpsert,
)
from app.db.models import User
from app.db.session import get_db_session
from app.repositories.products import ProductRepository
from app.services.plan_gate import FREE_MAX_PRODUCTS, require_within_limit
from app.services.profile import ProfileService

router = APIRouter(tags=["profile"])

# Every route below is tenant-scoped: the tenant_id comes from the authenticated
# user's JWT, never from the request body. The Wall holds here.
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(user: CurrentUser, db: Db) -> ProfileResponse:
    profile = await ProfileService(db).get_profile(tenant_id=user.tenant_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Profile not set yet")
    return ProfileResponse.model_validate(profile)


@router.put("/profile", response_model=ProfileResponse)
async def upsert_profile(payload: ProfileUpsert, user: CurrentUser, db: Db) -> ProfileResponse:
    profile = await ProfileService(db).upsert_profile(
        tenant_id=user.tenant_id, actor_id=user.id, data=payload
    )
    return ProfileResponse.model_validate(profile)


@router.get("/products", response_model=list[ProductResponse])
async def list_products(user: CurrentUser, db: Db) -> list[ProductResponse]:
    """This tenant's full catalog — used by the bill-review screen's product picker
    (Task 5.18) to map an OCR'd line to any product, tracked in inventory or not.
    Tenant-scoped (the Wall); never another tenant's catalog."""
    products = await ProfileService(db).list_products(tenant_id=user.tenant_id)
    return [ProductResponse.model_validate(p) for p in products]


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductWrite, user: CurrentUser, db: Db) -> ProductResponse:
    # Free plan: catalog capped (plan_gate) — Pro is unlimited.
    current = await ProductRepository(db).count(user.tenant_id)
    await require_within_limit(db, user.tenant_id, "products", current, FREE_MAX_PRODUCTS)
    product = await ProfileService(db).create_product(
        tenant_id=user.tenant_id, actor_id=user.id, data=payload
    )
    return ProductResponse.model_validate(product)


@router.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID, payload: ProductWrite, user: CurrentUser, db: Db
) -> ProductResponse:
    product = await ProfileService(db).update_product(
        tenant_id=user.tenant_id, actor_id=user.id, product_id=product_id, data=payload
    )
    return ProductResponse.model_validate(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: UUID, user: CurrentUser, db: Db) -> None:
    await ProfileService(db).delete_product(
        tenant_id=user.tenant_id, actor_id=user.id, product_id=product_id
    )


@router.get("/operating-hours", response_model=list[DayHoursResponse])
async def get_hours(user: CurrentUser, db: Db) -> list[DayHoursResponse]:
    rows = await ProfileService(db).get_hours(tenant_id=user.tenant_id)
    return [DayHoursResponse.model_validate(r) for r in rows]


@router.put("/operating-hours", response_model=list[DayHoursResponse])
async def replace_hours(
    payload: OperatingHoursReplace, user: CurrentUser, db: Db
) -> list[DayHoursResponse]:
    rows = await ProfileService(db).replace_hours(
        tenant_id=user.tenant_id, actor_id=user.id, data=payload
    )
    return [DayHoursResponse.model_validate(r) for r in rows]


@router.get("/policies", response_model=list[PolicyResponse])
async def get_policies(user: CurrentUser, db: Db) -> list[PolicyResponse]:
    rows = await ProfileService(db).get_policies(tenant_id=user.tenant_id)
    return [PolicyResponse.model_validate(r) for r in rows]


@router.put("/policies", response_model=list[PolicyResponse])
async def upsert_policies(
    payload: PoliciesUpsert, user: CurrentUser, db: Db
) -> list[PolicyResponse]:
    rows = await ProfileService(db).upsert_policies(
        tenant_id=user.tenant_id, actor_id=user.id, data=payload
    )
    return [PolicyResponse.model_validate(r) for r in rows]
