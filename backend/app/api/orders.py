from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.order.schemas import ConfirmedOrder, ParsedOrderItem
from app.api.deps import get_current_user
from app.api.schemas.orders import OrderItemRead, OrderRead, OrdersPage
from app.db.models import User
from app.db.session import get_db_session
from app.domain.errors import InsufficientStock, ProductNotInCatalog, ProductUnavailable
from app.repositories.customers import CustomerRepository
from app.repositories.orders import OrderRepository
from app.services.order_completion import (
    OrderCompletionService,
    OrderNotCompletable,
    OrderNotFound,
)
from app.services.orders import OrderService
from prompts import inventory_ar

router = APIRouter(tags=["orders"])

# Tenant scope comes from the authenticated user's JWT, never the request body.
CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


# ── Manual order schemas (Phase 8, Task 8.5) ─────────────────────────────────


class ManualOrderItem(BaseModel):
    product_id: UUID
    quantity: int = Field(ge=1)


class ManualOrderRequest(BaseModel):
    customer_phone: str = Field(min_length=4, max_length=32)
    items: list[ManualOrderItem] = Field(min_length=1)


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


@router.post("/orders/{order_id}/complete", response_model=OrderRead)
async def complete_order(order_id: UUID, request: Request, user: CurrentUser, db: Db) -> OrderRead:
    """Mark a confirmed order as completed, deducting every tracked line from
    inventory in one atomic transaction.

    The owner triggers this from the dashboard (the button lands in Task 4.14).
    Deduction happens on completion, not confirmation. 404 if the order isn't
    this tenant's; 409 if it isn't `confirmed` (e.g. already completed) or if any
    line is short on stock (the whole completion rolls back — no partial
    deduction). tenant_id comes from the JWT, never the path/body.

    After the completion commits, any product the deduction pushed at/below its
    reorder threshold gets a draft reorder PO from the InventoryAgent (Task 4.9),
    unless one is already open. The agent is the single lifespan-built instance on
    app.state, reached the same way the message dispatcher reaches the OrderAgent.
    """
    tenant_id = user.tenant_id
    try:
        order = await OrderCompletionService(
            db, inventory_agent=request.app.state.inventory_agent
        ).complete(tenant_id=tenant_id, order_id=order_id, actor_id=user.id)
    except OrderNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, inventory_ar.ORDER_NOT_FOUND) from None
    except OrderNotCompletable:
        raise HTTPException(status.HTTP_409_CONFLICT, inventory_ar.ORDER_NOT_COMPLETABLE) from None
    except InsufficientStock:
        raise HTTPException(status.HTTP_409_CONFLICT, inventory_ar.INSUFFICIENT_STOCK) from None

    repo = OrderRepository(db)
    items = await repo.items_for_orders(tenant_id, [order.id])
    return OrderRead(
        id=order.id,
        status=order.status,
        fulfillment_type=order.fulfillment_type,
        requested_time_text=order.requested_time_text,
        total_lbp=order.total_lbp,
        total_usd=order.total_usd,
        created_at=order.created_at,
        customer_id=order.customer_id,
        customer_display_name=None,
        items=[OrderItemRead.model_validate(i) for i in items],
    )


@router.post("/orders/manual", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_manual_order(
    body: ManualOrderRequest,
    user: CurrentUser,
    db: Db,
) -> OrderRead:
    """Dashboard owner creates an order manually — no AI, no ActionGate.

    The owner is the operator; direct dashboard entry IS the human approval
    (Constitution V). tenant_id comes exclusively from the JWT.

    404 if the customer phone is not found for this tenant.
    422 if any product_id is invalid or belongs to another tenant (The Wall).
    """
    tenant_id = user.tenant_id

    customer = await CustomerRepository(db).get_by_phone(tenant_id, body.customer_phone)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ما لقينا هالزبون بمحلك.")

    confirmed = ConfirmedOrder(
        items=[ParsedOrderItem(product_id=i.product_id, quantity=i.quantity) for i in body.items],
    )
    try:
        order = await OrderService(db).create_order(
            tenant_id=tenant_id,
            customer_id=customer.id,
            confirmed=confirmed,
            source="manual",
        )
    except ProductNotInCatalog:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "منتج غير موجود بكتالوج محلك."
        ) from None
    except ProductUnavailable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "منتج مش متوفر هلّق.") from None

    order_repo = OrderRepository(db)
    order_items = await order_repo.items_for_orders(tenant_id, [order.id])
    return OrderRead(
        id=order.id,
        status=order.status,
        fulfillment_type=order.fulfillment_type,
        requested_time_text=order.requested_time_text,
        total_lbp=order.total_lbp,
        total_usd=order.total_usd,
        created_at=order.created_at,
        customer_id=order.customer_id,
        customer_display_name=customer.display_name,
        items=[OrderItemRead.model_validate(i) for i in order_items],
    )
