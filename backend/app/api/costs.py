"""Owner-facing cost dashboard endpoint (Phase 8, Task 8.7).

GET /dashboard/costs — returns 30-day daily LLM cost data scoped to the
authenticated tenant. Powers the CostDashboardPage in the frontend.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db_session
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.business_policies import BusinessPolicyRepository

router = APIRouter(tags=["costs"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Db = Annotated[AsyncSession, Depends(get_db_session)]

_WINDOW_DAYS = 30


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
    tenant_id = user.tenant_id
    today = date.today()
    from_date = today - timedelta(days=_WINDOW_DAYS - 1)

    summary = await AgentRunRepository(db).daily_summary(
        tenant_id, from_date=from_date, to_date=today
    )

    budget_policy = await BusinessPolicyRepository(db).get_by_key(tenant_id, "daily_llm_budget_usd")
    budget_usd = float(budget_policy.value or 0) if budget_policy else 0.0

    days = []
    current = from_date
    while current <= today:
        agents = summary.get(current, {})
        days.append(
            {
                "date": str(current),
                "total_usd": round(sum(agents.values()), 6),
                "by_agent": {k: round(v, 6) for k, v in agents.items()},
            }
        )
        current += timedelta(days=1)

    return {"days": days, "budget_usd": budget_usd}
