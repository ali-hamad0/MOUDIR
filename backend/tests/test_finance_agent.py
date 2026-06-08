"""Task 7.3 — FinanceAgent tests.

Unit tests cover:
  - _compute_trend (pure function, no DB)
  - flag_anomalies (stub and spy detectors, sync)
  - get_revenue_summary (mock DB — verifies tenant scoping)
  - compose_reply (mock LLM — verifies LLM explains, not flags)
  - FinanceAgent.handle (smoke test, all stubs)
  - Cross-tenant isolation: tools pass ctx.tenant_id, never another tenant's id
"""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.finance.schemas import AnomalyResult, RevenueDay, RevenueSummary
from app.agents.finance.tools import (
    ToolContext,
    _compute_trend,
    compose_reply,
    flag_anomalies,
    get_revenue_summary,
)
from app.ml.predictors import StubAnomalyDetector


def _make_ctx(
    tenant_id=None,
    *,
    anomaly_detector=None,
    router=None,
    session=None,
    settings=None,
) -> ToolContext:
    return ToolContext(
        session=session or AsyncMock(),
        tenant_id=tenant_id or uuid.uuid4(),
        router=router or MagicMock(),
        settings=settings or MagicMock(llm_max_retries=1),
        anomaly_detector=anomaly_detector or StubAnomalyDetector(),
    )


def _revenue_days(revenues: list[int]) -> list[RevenueDay]:
    return [RevenueDay(day=date(2026, 1, i + 1), revenue_lbp=v) for i, v in enumerate(revenues)]


# ── _compute_trend (pure) ──────────────────────────────────────────────────────


class TestComputeTrend:
    def test_stable_when_fewer_than_14_days(self):
        assert _compute_trend(_revenue_days([1000] * 10)) == "stable"

    def test_stable_when_revenue_flat(self):
        assert _compute_trend(_revenue_days([1000] * 14)) == "stable"

    def test_up_when_recent_much_higher(self):
        assert _compute_trend(_revenue_days([1000] * 7 + [2000] * 7)) == "up"

    def test_down_when_recent_much_lower(self):
        assert _compute_trend(_revenue_days([1000] * 7 + [500] * 7)) == "down"

    def test_stable_when_prior_avg_is_zero(self):
        assert _compute_trend(_revenue_days([0] * 7 + [1000] * 7)) == "stable"

    def test_stable_near_boundary(self):
        # 5% change — below the 10% threshold
        assert _compute_trend(_revenue_days([1000] * 7 + [1050] * 7)) == "stable"

    def test_exactly_at_up_boundary(self):
        # 11% increase → up
        assert _compute_trend(_revenue_days([1000] * 7 + [1110] * 7)) == "up"


# ── flag_anomalies ─────────────────────────────────────────────────────────────


class TestFlagAnomalies:
    def test_stub_detector_returns_no_anomalies(self):
        ctx = _make_ctx()
        rows = [(date(2026, 1, 1), 1000), (date(2026, 1, 2), 1200)]
        result = flag_anomalies(ctx, rows)
        assert result.anomaly_count == 0
        assert result.total_days == 2
        assert result.flagged_days == []

    def test_custom_detector_flags_are_respected(self):
        flagged_date = date(2026, 1, 3)

        class _SpyDetector:
            def flag_days(self, tenant_id, revenue_history, *, window=14):
                return {flagged_date: True, date(2026, 1, 1): False}

        rows = [(date(2026, 1, 1), 500), (date(2026, 1, 2), 600), (flagged_date, 50)]
        ctx = _make_ctx(anomaly_detector=_SpyDetector())
        result = flag_anomalies(ctx, rows)
        assert result.anomaly_count == 1
        assert result.flagged_days[0].day == flagged_date
        assert result.flagged_days[0].revenue_lbp == 50

    def test_detector_called_with_ctx_tenant_id(self):
        """The Wall: flag_anomalies passes ctx.tenant_id to the detector, not another tenant's."""
        tenant_id = uuid.uuid4()
        other_tenant = uuid.uuid4()
        seen: list = []

        class _SpyDetector:
            def flag_days(self, t_id, history, *, window=14):
                seen.append(t_id)
                return {}

        ctx = _make_ctx(tenant_id=tenant_id, anomaly_detector=_SpyDetector())
        flag_anomalies(ctx, [])
        assert seen == [tenant_id]
        assert other_tenant not in seen

    def test_total_days_matches_row_count(self):
        rows = [(date(2026, 1, i + 1), 100) for i in range(10)]
        ctx = _make_ctx()
        result = flag_anomalies(ctx, rows)
        assert result.total_days == 10


# ── get_revenue_summary (mock DB) ─────────────────────────────────────────────


class TestGetRevenueSummary:
    async def test_calls_daily_revenue_with_ctx_tenant_id(self):
        """The Wall: the tool queries only ctx.tenant_id's revenue."""
        tenant_id = uuid.uuid4()
        other_tenant = uuid.uuid4()

        mock_repo = AsyncMock()
        mock_repo.daily_revenue.return_value = []
        ctx = _make_ctx(tenant_id=tenant_id)

        with patch("app.agents.finance.tools.TrainingDataRepository", return_value=mock_repo):
            await get_revenue_summary(ctx, days=7)

        called_tenant = mock_repo.daily_revenue.call_args[0][0]
        assert called_tenant == tenant_id
        assert called_tenant != other_tenant

    async def test_total_lbp_sums_rows(self):
        rows = [(date(2026, 1, 1), 1000), (date(2026, 1, 2), 2000)]
        mock_repo = AsyncMock()
        mock_repo.daily_revenue.return_value = rows

        with patch("app.agents.finance.tools.TrainingDataRepository", return_value=mock_repo):
            _, summary = await get_revenue_summary(_make_ctx(), days=7)

        assert summary.total_lbp == 3000

    async def test_empty_history_gives_zero_total(self):
        mock_repo = AsyncMock()
        mock_repo.daily_revenue.return_value = []

        with patch("app.agents.finance.tools.TrainingDataRepository", return_value=mock_repo):
            _, summary = await get_revenue_summary(_make_ctx(), days=30)

        assert summary.total_lbp == 0
        assert summary.daily_avg_lbp == 0

    async def test_returns_raw_rows_unchanged(self):
        """Raw rows must be returned as-is so the anomaly detector receives the same data."""
        rows = [(date(2026, 1, 1), 500)]
        mock_repo = AsyncMock()
        mock_repo.daily_revenue.return_value = rows

        with patch("app.agents.finance.tools.TrainingDataRepository", return_value=mock_repo):
            raw, _ = await get_revenue_summary(_make_ctx(), days=7)

        assert raw is rows

    async def test_summary_days_window_matches_requested_days(self):
        mock_repo = AsyncMock()
        mock_repo.daily_revenue.return_value = []

        with patch("app.agents.finance.tools.TrainingDataRepository", return_value=mock_repo):
            _, summary = await get_revenue_summary(_make_ctx(), days=14)

        assert summary.days_window == 14


# ── compose_reply (mock LLM) ──────────────────────────────────────────────────


class TestComposeReply:
    def _summary(self) -> RevenueSummary:
        return RevenueSummary(
            days_window=30,
            total_lbp=3_000_000,
            daily_avg_lbp=100_000,
            trend="stable",
            days=[],
        )

    def _no_anomalies(self) -> AnomalyResult:
        return AnomalyResult(flagged_days=[], total_days=30, anomaly_count=0)

    async def test_returns_llm_reply_when_successful(self):
        from app.agents.finance.schemas import FinanceReply

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = FinanceReply(reply_ar="المبيعات تمام هالشهر.")
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        reply = await compose_reply(
            ctx,
            question="كيف المبيعات؟",
            summary=self._summary(),
            anomaly_result=self._no_anomalies(),
        )
        assert reply == "المبيعات تمام هالشهر."

    async def test_returns_fallback_on_llm_error(self):
        from prompts import finance_agent_ar

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = RuntimeError("provider down")
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        reply = await compose_reply(
            ctx,
            question="كيف المبيعات؟",
            summary=self._summary(),
            anomaly_result=self._no_anomalies(),
        )
        assert reply == finance_agent_ar.FALLBACK_REPLY

    async def test_llm_receives_ml_flagged_data_in_prompt(self):
        """The LLM explains the ML-flagged days — it does not decide them."""
        from app.agents.finance.schemas import FinanceReply

        flagged = AnomalyResult(
            flagged_days=[RevenueDay(day=date(2026, 1, 5), revenue_lbp=100)],
            total_days=30,
            anomaly_count=1,
        )
        captured: list = []

        async def _capture(messages, **_):
            captured.extend(messages)
            return FinanceReply(reply_ar="في يوم غير عادي.")

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = _capture
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        await compose_reply(
            ctx,
            question="شو صار؟",
            summary=self._summary(),
            anomaly_result=flagged,
        )
        prompt_text = captured[0].content
        # The ML result (flagged date and revenue) must be present in the prompt
        assert "2026-01-05" in prompt_text
        assert "100" in prompt_text

    async def test_no_anomaly_section_when_all_clean(self):
        """The 'no anomalies' message is injected into the prompt when the detector found nothing."""
        from app.agents.finance.schemas import FinanceReply
        from prompts import finance_agent_ar

        captured: list = []

        async def _capture(messages, **_):
            captured.extend(messages)
            return FinanceReply(reply_ar="كل شي تمام.")

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = _capture
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        await compose_reply(
            ctx,
            question="كيف المبيعات؟",
            summary=self._summary(),
            anomaly_result=self._no_anomalies(),
        )
        assert finance_agent_ar.NO_ANOMALY in captured[0].content


# ── FinanceAgent.handle (smoke test) ──────────────────────────────────────────


class TestFinanceAgent:
    async def test_handle_returns_non_empty_string(self):
        """Smoke test: handle runs the full graph and returns a reply string."""
        from app.agents.finance.agent import FinanceAgent
        from app.agents.finance.schemas import FinanceReply

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = FinanceReply(reply_ar="كل شي تمام.")
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        mock_session = AsyncMock()
        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessionmaker.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_repo = AsyncMock()
        mock_repo.daily_revenue.return_value = []

        with patch("app.agents.finance.tools.TrainingDataRepository", return_value=mock_repo):
            agent = FinanceAgent(
                router=mock_router,
                settings=MagicMock(llm_max_retries=1),
                sessionmaker=mock_sessionmaker,
            )
            reply = await agent.handle("كيف المبيعات؟", uuid.uuid4(), days=7)

        assert isinstance(reply, str)
        assert len(reply) > 0

    async def test_handle_uses_stub_detector_by_default(self):
        """FinanceAgent defaults to StubAnomalyDetector when no detector is injected."""
        from app.agents.finance.agent import FinanceAgent
        from app.agents.finance.schemas import FinanceReply

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = FinanceReply(reply_ar="ما في أخبار.")
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_sessionmaker.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_repo = AsyncMock()
        mock_repo.daily_revenue.return_value = []

        with patch("app.agents.finance.tools.TrainingDataRepository", return_value=mock_repo):
            # No anomaly_detector argument → uses StubAnomalyDetector → no crash
            agent = FinanceAgent(
                router=mock_router,
                settings=MagicMock(llm_max_retries=0),
                sessionmaker=mock_sessionmaker,
            )
            reply = await agent.handle("شو المجموع؟", uuid.uuid4())

        assert isinstance(reply, str)
