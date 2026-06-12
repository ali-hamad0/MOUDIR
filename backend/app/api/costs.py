"""Owner-facing cost dashboard endpoint (Phase 8, Task 8.7).

GET /dashboard/costs — returns 30-day daily LLM cost data scoped to the
authenticated tenant. Powers the CostDashboardPage in the frontend.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db_session
from app.services.cost_dashboard import build_daily_costs

router = APIRouter(tags=["costs"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/dashboard/costs")
async def get_dashboard_costs(user: CurrentUser, db: Db) -> dict:
    """30-day daily LLM cost breakdown for the authenticated tenant.

    Response shape:
    {
      "days": [{"date": "YYYY-MM-DD", "total_usd": 0.0012, "by_agent": {"supervisor": 0.0008, ...}}],
      "budget_usd": 10.0
    }

    Days with no spend are included with total_usd=0 so the frontend can render
    a continuous bar chart. The Wall: tenant_id comes from the JWT only.
    """
    return await build_daily_costs(db, user.tenant_id)
