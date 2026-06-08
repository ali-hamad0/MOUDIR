"""AgentRun repository — cost tracking for per-LLM-call rows (Task 7.9).

The Wall is enforced here: every read filters by tenant_id via the
TenantScopedRepository base. The admin endpoint aggregates within a tenant;
the callback writes within a tenant. No cross-tenant query exists here.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import cast, func, select
from sqlalchemy.types import Date

from app.db.models import AgentRun
from app.repositories.base import TenantScopedRepository


class AgentRunRepository(TenantScopedRepository[AgentRun]):
    """Tenant-scoped reads and writes for the agent_runs table."""

    model = AgentRun

    async def create(
        self,
        *,
        tenant_id: UUID,
        agent_name: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
    ) -> AgentRun:
        row = AgentRun(
            tenant_id=tenant_id,
            agent_name=agent_name,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=Decimal(str(cost_usd)),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def daily_summary(
        self,
        tenant_id: UUID,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[date, dict[str, float]]:
        """Return {date: {agent_name: total_cost_usd}} for a tenant, optionally
        filtered by date range. The Wall: scoped to tenant_id only.
        """
        day_col = cast(AgentRun.created_at, Date).label("day")
        stmt = (
            select(
                day_col,
                AgentRun.agent_name,
                func.sum(AgentRun.cost_usd).label("total_cost"),
            )
            .where(AgentRun.tenant_id == tenant_id)
            .group_by(day_col, AgentRun.agent_name)
            .order_by(day_col)
        )
        if from_date is not None:
            stmt = stmt.where(cast(AgentRun.created_at, Date) >= from_date)
        if to_date is not None:
            stmt = stmt.where(cast(AgentRun.created_at, Date) <= to_date)

        rows = (await self._session.execute(stmt)).all()
        result: dict[date, dict[str, float]] = {}
        for row in rows:
            d = row.day
            if d not in result:
                result[d] = {}
            result[d][row.agent_name] = float(row.total_cost or 0)
        return result
