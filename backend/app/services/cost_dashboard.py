"""Daily LLM cost dashboard data, shared by the owner and founder endpoints.

One builder so GET /dashboard/costs (owner) and GET /admin/tenants/{id}/costs
(founder) return the exact same shape and the frontend renders both with the
same chart component. The Wall: everything here is scoped to one tenant_id.
"""

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_runs import AgentRunRepository
from app.repositories.business_policies import BusinessPolicyRepository

WINDOW_DAYS = 30


async def build_daily_costs(
    db: AsyncSession, tenant_id: UUID, *, window_days: int = WINDOW_DAYS
) -> dict:
    """Return {"days": [{"date", "total_usd", "by_agent"}], "budget_usd"} for
    one tenant. Days with no spend are included with total_usd=0 so the chart
    is continuous.
    """
    today = date.today()
    from_date = today - timedelta(days=window_days - 1)

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
