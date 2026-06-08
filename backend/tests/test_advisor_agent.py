"""Task 7.5 — AdvisorAgent tests.

Unit tests cover:
  - get_todays_snapshot (tenant isolation; zero-data path)
  - get_demand_forecast (stub predictor; predictor called with ctx.tenant_id)
  - get_churn_summary (stub predictor; at-risk count; tenant isolation)
  - get_anomaly_status (stub detector; flagged-days mapping)
  - compose_briefing (LLM success; fallback on error; structured inputs in prompt)
  - AdvisorAgent.handle (smoke; no commit — read-only)
"""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.advisor.schemas import (
    AnomalyStatus,
    Briefing,
    ChurnSummary,
    DemandForecast,
    DemandForecastItem,
    ProductRevenue,
    TodaySnapshot,
)
from app.agents.advisor.tools import (
    ToolContext,
    compose_briefing,
    get_anomaly_status,
    get_churn_summary,
    get_demand_forecast,
    get_todays_snapshot,
)
from app.ml.predictors import StubAnomalyDetector, StubChurnPredictor, StubDemandPredictor

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_ctx(
    tenant_id=None,
    *,
    demand_predictor=None,
    churn_predictor=None,
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
        demand_predictor=demand_predictor or StubDemandPredictor(),
        churn_predictor=churn_predictor or StubChurnPredictor(),
        anomaly_detector=anomaly_detector or StubAnomalyDetector(),
    )


def _empty_snapshot() -> TodaySnapshot:
    return TodaySnapshot(
        today_revenue_lbp=0,
        avg_7day_lbp=0,
        same_day_last_week_lbp=0,
        order_count=0,
        top_products=[],
    )


def _empty_demand() -> DemandForecast:
    return DemandForecast(items=[])


def _empty_churn() -> ChurnSummary:
    return ChurnSummary(count_at_risk=0, total_customers=0, top_names=[])


def _empty_anomaly() -> AnomalyStatus:
    return AnomalyStatus(is_today_anomalous=False, recent_flag_count=0, recent_flags=[])


# ── get_todays_snapshot ────────────────────────────────────────────────────────


class TestGetTodaysSnapshot:
    async def test_returns_zero_revenue_when_no_orders(self):
        """With empty DB reads the snapshot has zero revenue and no products."""
        mock_tr = AsyncMock()
        mock_tr.daily_revenue.return_value = []
        mock_or = AsyncMock()
        mock_or.count_today.return_value = 0

        ctx = _make_ctx()
        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))
        ctx.session = mock_session

        with (
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.advisor.tools.OrderRepository", return_value=mock_or),
            patch(
                "app.agents.advisor.tools._top_products_today",
                new=AsyncMock(return_value=[]),
            ),
        ):
            snap = await get_todays_snapshot(ctx)

        assert snap.today_revenue_lbp == 0
        assert snap.avg_7day_lbp == 0
        assert snap.same_day_last_week_lbp == 0
        assert snap.order_count == 0
        assert snap.top_products == []

    async def test_computes_avg_7day_from_revenue_rows(self):
        """Average of the 7-day window rows is computed correctly."""
        day = date(2026, 1, 15)
        mock_rows = [(day, 10_000_000), (day, 20_000_000)]  # avg = 15_000_000

        mock_tr = AsyncMock()
        mock_tr.daily_revenue.side_effect = lambda t_id, **kw: (
            [] if kw.get("start") == date.today() else mock_rows
        )
        mock_or = AsyncMock()
        mock_or.count_today.return_value = 3

        ctx = _make_ctx()
        with (
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.advisor.tools.OrderRepository", return_value=mock_or),
            patch(
                "app.agents.advisor.tools._top_products_today",
                new=AsyncMock(return_value=[]),
            ),
        ):
            snap = await get_todays_snapshot(ctx)

        assert snap.avg_7day_lbp == 15_000_000

    async def test_passes_tenant_id_to_every_read(self):
        """The Wall: daily_revenue and count_today receive the exact ctx.tenant_id."""
        tenant_id = uuid.uuid4()
        other_tenant = uuid.uuid4()
        seen_tr: list = []
        seen_or: list = []

        mock_tr = AsyncMock()
        mock_tr.daily_revenue.side_effect = lambda t_id, **kw: (seen_tr.append(t_id) or [])
        mock_or = AsyncMock()
        mock_or.count_today.side_effect = lambda t_id, **kw: (seen_or.append(t_id) or 0)

        ctx = _make_ctx(tenant_id=tenant_id)
        with (
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.advisor.tools.OrderRepository", return_value=mock_or),
            patch(
                "app.agents.advisor.tools._top_products_today",
                new=AsyncMock(return_value=[]),
            ),
        ):
            await get_todays_snapshot(ctx)

        assert all(t == tenant_id for t in seen_tr)
        assert all(t == tenant_id for t in seen_or)
        assert other_tenant not in seen_tr
        assert other_tenant not in seen_or


# ── get_demand_forecast ────────────────────────────────────────────────────────


class TestGetDemandForecast:
    async def test_returns_empty_when_no_low_stock(self):
        ctx = _make_ctx()
        with patch(
            "app.agents.advisor.tools._low_stock_with_names",
            new=AsyncMock(return_value=[]),
        ):
            result = await get_demand_forecast(ctx)

        assert result.items == []

    async def test_predictor_called_with_ctx_tenant_id(self):
        """The Wall: demand_predictor.predict_quantity receives ctx.tenant_id."""
        tenant_id = uuid.uuid4()
        other_tenant = uuid.uuid4()
        seen: list = []

        class _SpyPredictor:
            def predict_quantity(self, t_id, product_id, history, *, as_of=None):
                seen.append(t_id)
                return None

        inv = MagicMock()
        inv.product_id = uuid.uuid4()
        inv.quantity = 2

        mock_tr = AsyncMock()
        mock_tr.daily_product_demand.return_value = []

        ctx = _make_ctx(tenant_id=tenant_id, demand_predictor=_SpyPredictor())
        with (
            patch(
                "app.agents.advisor.tools._low_stock_with_names",
                new=AsyncMock(return_value=[(inv, "خبز")]),
            ),
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
        ):
            await get_demand_forecast(ctx)

        assert seen == [tenant_id]
        assert other_tenant not in seen

    async def test_none_prediction_becomes_zero(self):
        """StubDemandPredictor returns None → predicted_units_7d is 0."""
        inv = MagicMock()
        inv.product_id = uuid.uuid4()
        inv.quantity = 1

        mock_tr = AsyncMock()
        mock_tr.daily_product_demand.return_value = []

        ctx = _make_ctx()
        with (
            patch(
                "app.agents.advisor.tools._low_stock_with_names",
                new=AsyncMock(return_value=[(inv, "حليب")]),
            ),
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
        ):
            result = await get_demand_forecast(ctx)

        assert len(result.items) == 1
        assert result.items[0].predicted_units_7d == 0
        assert result.items[0].product_name == "حليب"
        assert result.items[0].current_stock == 1


# ── get_churn_summary ──────────────────────────────────────────────────────────


class TestGetChurnSummary:
    async def test_stub_predictor_returns_zero_risks(self):
        mock_tr = AsyncMock()
        mock_tr.customer_orders.return_value = []

        ctx = _make_ctx()
        with patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr):
            result = await get_churn_summary(ctx)

        assert result.count_at_risk == 0
        assert result.total_customers == 0
        assert result.top_names == []

    async def test_counts_at_risk_above_threshold(self):
        """Customers with score > 0.5 count as at-risk."""
        cid1 = str(uuid.uuid4())
        cid2 = str(uuid.uuid4())
        cid3 = str(uuid.uuid4())

        class _SpyPredictor:
            def predict_risks(self, t_id, orders, *, as_of=None):
                return {cid1: 0.9, cid2: 0.3, cid3: 0.7}  # 2 at-risk

        mock_tr = AsyncMock()
        mock_tr.customer_orders.return_value = []

        mock_customer = MagicMock()
        mock_customer.display_name = "أبو خالد"
        mock_cr = AsyncMock()
        mock_cr.get.return_value = mock_customer

        ctx = _make_ctx(churn_predictor=_SpyPredictor())
        with (
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.advisor.tools.CustomerRepository", return_value=mock_cr),
        ):
            result = await get_churn_summary(ctx)

        assert result.count_at_risk == 2
        assert result.total_customers == 3

    async def test_predictor_called_with_ctx_tenant_id(self):
        """The Wall: churn_predictor.predict_risks receives ctx.tenant_id."""
        tenant_id = uuid.uuid4()
        other_tenant = uuid.uuid4()
        seen: list = []

        class _SpyPredictor:
            def predict_risks(self, t_id, orders, *, as_of=None):
                seen.append(t_id)
                return {}

        mock_tr = AsyncMock()
        mock_tr.customer_orders.return_value = []

        ctx = _make_ctx(tenant_id=tenant_id, churn_predictor=_SpyPredictor())
        with patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr):
            await get_churn_summary(ctx)

        assert seen == [tenant_id]
        assert other_tenant not in seen


# ── get_anomaly_status ─────────────────────────────────────────────────────────


class TestGetAnomalyStatus:
    async def test_stub_detector_no_flags(self):
        mock_tr = AsyncMock()
        mock_tr.daily_revenue.return_value = []

        ctx = _make_ctx()
        with patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr):
            result = await get_anomaly_status(ctx)

        assert result.is_today_anomalous is False
        assert result.recent_flag_count == 0
        assert result.recent_flags == []

    async def test_flagged_days_propagated(self):
        """Detector output is correctly mapped to recent_flags and is_today_anomalous."""
        today = date.today()
        yesterday = date(today.year, today.month, today.day - 1) if today.day > 1 else today

        class _SpyDetector:
            def flag_days(self, t_id, revenue_history, *, window=14):
                return {today: True, yesterday: False}

        mock_tr = AsyncMock()
        mock_tr.daily_revenue.return_value = []

        ctx = _make_ctx(anomaly_detector=_SpyDetector())
        with patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr):
            result = await get_anomaly_status(ctx)

        assert result.is_today_anomalous is True
        assert result.recent_flag_count == 1
        assert today in result.recent_flags

    async def test_detector_called_with_ctx_tenant_id(self):
        """The Wall: anomaly_detector.flag_days receives ctx.tenant_id."""
        tenant_id = uuid.uuid4()
        other_tenant = uuid.uuid4()
        seen: list = []

        class _SpyDetector:
            def flag_days(self, t_id, revenue_history, *, window=14):
                seen.append(t_id)
                return {}

        mock_tr = AsyncMock()
        mock_tr.daily_revenue.return_value = []

        ctx = _make_ctx(tenant_id=tenant_id, anomaly_detector=_SpyDetector())
        with patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr):
            await get_anomaly_status(ctx)

        assert seen == [tenant_id]
        assert other_tenant not in seen


# ── compose_briefing ───────────────────────────────────────────────────────────


class TestComposeBriefing:
    async def test_returns_llm_briefing_on_success(self):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = Briefing(
            briefing_ar="صباح الخير! مبيعاتك كتير منيحة اليوم."
        )
        mock_router = MagicMock()
        mock_router.tier2.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        result = await compose_briefing(
            ctx,
            snapshot=_empty_snapshot(),
            demand=_empty_demand(),
            churn=_empty_churn(),
            anomaly=_empty_anomaly(),
        )

        assert result == "صباح الخير! مبيعاتك كتير منيحة اليوم."

    async def test_returns_fallback_on_llm_error(self):
        from prompts import advisor_agent_ar

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = RuntimeError("provider down")
        mock_router = MagicMock()
        mock_router.tier2.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        result = await compose_briefing(
            ctx,
            snapshot=_empty_snapshot(),
            demand=_empty_demand(),
            churn=_empty_churn(),
            anomaly=_empty_anomaly(),
        )

        assert result == advisor_agent_ar.FALLBACK_BRIEFING

    async def test_structured_data_appears_in_system_message(self):
        """Constitution IV: the LLM receives structured numbers in the prompt, not raw DB."""
        captured: list = []

        async def _capture(messages, **_):
            captured.extend(messages)
            return Briefing(briefing_ar="تمام")

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = _capture
        mock_router = MagicMock()
        mock_router.tier2.return_value.with_structured_output.return_value = mock_model

        snap = TodaySnapshot(
            today_revenue_lbp=5_000_000,
            avg_7day_lbp=4_000_000,
            same_day_last_week_lbp=3_000_000,
            order_count=12,
            top_products=[ProductRevenue(name_ar="خبز", revenue_lbp=500_000)],
        )
        churn = ChurnSummary(count_at_risk=3, total_customers=10, top_names=["أبو خالد"])
        anomaly = AnomalyStatus(is_today_anomalous=True, recent_flag_count=2, recent_flags=[])
        demand = DemandForecast(
            items=[DemandForecastItem(product_name="حليب", current_stock=2, predicted_units_7d=8)]
        )

        ctx = _make_ctx(router=mock_router)
        await compose_briefing(ctx, snapshot=snap, demand=demand, churn=churn, anomaly=anomaly)

        prompt_text = captured[0].content
        assert "5,000,000" in prompt_text  # today_revenue
        assert "3" in prompt_text  # count_at_risk
        assert "أبو خالد" in prompt_text
        assert "نعم" in prompt_text  # is_today_anomalous

    async def test_uses_tier2_llm(self):
        """Advisor briefing MUST use the Tier 2 model (Gemini Pro), not Tier 1."""
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = Briefing(briefing_ar="تمام")
        mock_router = MagicMock()
        mock_router.tier2.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        await compose_briefing(
            ctx,
            snapshot=_empty_snapshot(),
            demand=_empty_demand(),
            churn=_empty_churn(),
            anomaly=_empty_anomaly(),
        )

        mock_router.tier2.assert_called_once()
        mock_router.tier1.assert_not_called()


# ── AdvisorAgent.handle smoke tests ───────────────────────────────────────────


class TestAdvisorAgent:
    def _make_sessionmaker(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessionmaker.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_sessionmaker, mock_session

    async def test_handle_returns_string(self):
        """Smoke test: AdvisorAgent.handle returns a non-empty string."""
        from app.agents.advisor.agent import AdvisorAgent

        mock_sessionmaker, _ = self._make_sessionmaker()
        mock_tr = AsyncMock()
        mock_tr.daily_revenue.return_value = []
        mock_tr.customer_orders.return_value = []
        mock_or = AsyncMock()
        mock_or.count_today.return_value = 0

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = Briefing(briefing_ar="يوم حلو بإذن الله!")
        mock_router = MagicMock()
        mock_router.tier2.return_value.with_structured_output.return_value = mock_model

        with (
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.advisor.tools.OrderRepository", return_value=mock_or),
            patch(
                "app.agents.advisor.tools._top_products_today",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.agents.advisor.tools._low_stock_with_names",
                new=AsyncMock(return_value=[]),
            ),
        ):
            agent = AdvisorAgent(
                router=mock_router,
                settings=MagicMock(llm_max_retries=1),
                sessionmaker=mock_sessionmaker,
            )
            reply = await agent.handle("كيف محلي اليوم؟", uuid.uuid4())

        assert isinstance(reply, str)
        assert len(reply) > 0

    async def test_handle_never_commits(self):
        """AdvisorAgent is read-only — session.commit must never be called."""
        from app.agents.advisor.agent import AdvisorAgent

        mock_sessionmaker, mock_session = self._make_sessionmaker()
        mock_tr = AsyncMock()
        mock_tr.daily_revenue.return_value = []
        mock_tr.customer_orders.return_value = []
        mock_or = AsyncMock()
        mock_or.count_today.return_value = 0

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = Briefing(briefing_ar="تمام")
        mock_router = MagicMock()
        mock_router.tier2.return_value.with_structured_output.return_value = mock_model

        with (
            patch("app.agents.advisor.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.advisor.tools.OrderRepository", return_value=mock_or),
            patch(
                "app.agents.advisor.tools._top_products_today",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.agents.advisor.tools._low_stock_with_names",
                new=AsyncMock(return_value=[]),
            ),
        ):
            agent = AdvisorAgent(
                router=mock_router,
                settings=MagicMock(llm_max_retries=0),
                sessionmaker=mock_sessionmaker,
            )
            await agent.handle("صباح الخير", uuid.uuid4())

        mock_session.commit.assert_not_called()
