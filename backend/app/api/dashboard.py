"""Dashboard summary API (Phase 10) — one call powering the home-page charts.

GET /dashboard/summary returns everything the at-a-glance view needs:
  - daily revenue + order counts for the trailing 14 Beirut days (gap-filled,
    so the chart x-axis is continuous),
  - today's KPIs (orders, revenue),
  - the 7-day top products and order-status mix,
  - inventory health (low-stock vs total).

Auth: get_current_user (JWT). Every aggregate underneath is tenant-scoped
through the repositories — the Wall.
"""

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db_session
from app.repositories.inventory import InventoryRepository
from app.repositories.orders import BEIRUT_TZ, OrderRepository

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]

_TREND_DAYS = 14
_TOP_DAYS = 7
_TOP_K = 5


class DailyPoint(BaseModel):
    day: date
    orders: int
    revenue_lbp: int


class TopProduct(BaseModel):
    name: str
    units: int


class StatusCount(BaseModel):
    status: str
    count: int


class DashboardSummary(BaseModel):
    daily: list[DailyPoint]  # trailing 14 days, gap-filled, oldest first
    today_orders: int
    today_revenue_lbp: int
    top_products: list[TopProduct]  # 7-day best sellers
    order_status: list[StatusCount]  # 7-day status mix
    low_stock_count: int
    total_products: int


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(user: CurrentUser, db: Db) -> DashboardSummary:
    orders = OrderRepository(db)
    inventory = InventoryRepository(db)
    tenant_id = user.tenant_id

    rows = await orders.daily_totals(tenant_id, days=_TREND_DAYS)
    by_day = {row.day: row for row in rows}
    today = datetime.now(BEIRUT_TZ).date()
    daily: list[DailyPoint] = []
    for offset in range(_TREND_DAYS - 1, -1, -1):
        day = today - timedelta(days=offset)
        row = by_day.get(day)
        daily.append(
            DailyPoint(
                day=day,
                orders=int(row.orders) if row else 0,
                revenue_lbp=int(row.revenue_lbp) if row else 0,
            )
        )

    top = await orders.top_products(tenant_id, days=_TOP_DAYS, k=_TOP_K)
    statuses = await orders.status_counts(tenant_id, days=_TOP_DAYS)
    low_stock = await inventory.list_low_stock(tenant_id)
    total_products = await inventory.count_products(tenant_id)

    return DashboardSummary(
        daily=daily,
        today_orders=daily[-1].orders,
        today_revenue_lbp=daily[-1].revenue_lbp,
        top_products=[TopProduct(name=r.name, units=int(r.units)) for r in top],
        order_status=[StatusCount(status=r.status, count=int(r.count)) for r in statuses],
        low_stock_count=len(low_stock),
        total_products=total_products,
    )
