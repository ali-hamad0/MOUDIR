from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.orders import OrderItemRead, OrderRead, OrdersPage
from app.db.models import User
from app.db.session import get_db_session
from app.repositories.orders import OrderRepository

router = APIRouter(tags=["orders"])

# Tenant scope comes from the authenticated user's JWT, never the request body.
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/orders/today", response_model=OrdersPage)
async def orders_today(
    user: CurrentUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrdersPage:
    """Today's orders for the logged-in tenant, newest first, paginated.

    Powers the live dashboard feed (polled every 5s). "Today" is a Beirut
    calendar day. Items are batch-loaded in one query to avoid an N+1 over the
    page. limit is capped (≤100) so the endpoint can never be asked for an
    unbounded load.
    """
    repo = OrderRepository(db)
    tenant_id = user.tenant_id

    total = await repo.count_today(tenant_id)
    rows = await repo.list_today(tenant_id, limit=limit, offset=offset)

    orders = [order for order, _name in rows]
    names = {order.id: name for order, name in rows}

    # One batched query for every line on this page, then group by order.
    items = await repo.items_for_orders(tenant_id, [o.id for o in orders])
    items_by_order: dict = {}
    for item in items:
        items_by_order.setdefault(item.order_id, []).append(item)

    page = [
        OrderRead(
            id=order.id,
            status=order.status,
            fulfillment_type=order.fulfillment_type,
            requested_time_text=order.requested_time_text,
            total_lbp=order.total_lbp,
            total_usd=order.total_usd,
            created_at=order.created_at,
            customer_id=order.customer_id,
            customer_display_name=names.get(order.id),
            items=[OrderItemRead.model_validate(i) for i in items_by_order.get(order.id, [])],
        )
        for order in orders
    ]
    return OrdersPage(items=page, total=total, limit=limit, offset=offset)
