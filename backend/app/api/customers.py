from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.customers import CustomerRead, CustomersPage
from app.db.models import User
from app.db.session import get_db_session
from app.repositories.customers import CustomerRepository

router = APIRouter(tags=["customers"])

# Tenant scope comes from the authenticated user's JWT, never the request body.
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/customers", response_model=CustomersPage)
async def list_customers(
    user: CurrentUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomersPage:
    """This tenant's customers with order stats (count, total spent, last order),
    most-recently-active first, paginated. Scoped to the JWT tenant only."""
    repo = CustomerRepository(db)
    tenant_id = user.tenant_id

    total = await repo.count(tenant_id)
    rows = await repo.list_with_stats(tenant_id, limit=limit, offset=offset)

    items = [
        CustomerRead(
            id=customer.id,
            display_name=customer.display_name,
            phone_number=customer.phone_number,
            first_seen_at=customer.first_seen_at,
            order_count=order_count,
            total_spent_lbp=total_spent_lbp,
            total_spent_usd=total_spent_usd,
            last_order_at=last_order_at,
        )
        for (
            customer,
            order_count,
            total_spent_lbp,
            total_spent_usd,
            last_order_at,
        ) in rows
    ]
    return CustomersPage(items=items, total=total, limit=limit, offset=offset)
