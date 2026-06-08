"""Task 7.4 — CustomerAgent tests.

Unit tests cover:
  - get_churn_risks (stub and spy predictors; tenant isolation)
  - draft_reengagement (mock LLM; fallback on failure)
  - queue_reengagement (draft created; idempotency; no WhatsApp send)
  - _build_owner_reply (no risks path; risks + queued action path)
  - CustomerAgent.handle (smoke tests: no-risks path; at-risk + queued path)
"""

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.customer.schemas import ChurnRisks, CustomerRisk, QueuedAction
from app.agents.customer.tools import (
    ToolContext,
    _build_owner_reply,
    draft_reengagement,
    get_churn_risks,
    queue_reengagement,
)
from app.ml.predictors import StubChurnPredictor


def _make_ctx(
    tenant_id=None,
    *,
    churn_predictor=None,
    router=None,
    session=None,
    settings=None,
) -> ToolContext:
    return ToolContext(
        session=session or AsyncMock(),
        tenant_id=tenant_id or uuid.uuid4(),
        router=router or MagicMock(),
        settings=settings or MagicMock(llm_max_retries=1),
        churn_predictor=churn_predictor or StubChurnPredictor(),
    )


def _make_customer_risk(tenant_id=None, risk_score=0.8) -> CustomerRisk:
    return CustomerRisk(
        customer_id=uuid.uuid4(),
        customer_name_ar="أبو خالد",
        phone_number="+96176000001",
        risk_score=risk_score,
        last_order_at=date(2026, 1, 1),
    )


# ── get_churn_risks ────────────────────────────────────────────────────────────


class TestGetChurnRisks:
    async def test_stub_predictor_returns_no_risks(self):
        mock_repo = AsyncMock()
        mock_repo.customer_orders.return_value = []
        ctx = _make_ctx()

        with patch("app.agents.customer.tools.TrainingDataRepository", return_value=mock_repo):
            result = await get_churn_risks(ctx)

        assert result.customers == []
        assert result.total_at_risk == 0

    async def test_passes_tenant_id_to_predictor_and_repo(self):
        """The Wall: both the order fetch and the predictor call use ctx.tenant_id."""
        tenant_id = uuid.uuid4()
        other_tenant = uuid.uuid4()
        seen_by_predictor: list = []
        seen_by_repo: list = []

        class _SpyPredictor:
            def predict_risks(self, t_id, orders, *, as_of=None):
                seen_by_predictor.append(t_id)
                return {}

        mock_repo = AsyncMock()
        mock_repo.customer_orders.side_effect = lambda t_id, **_: (seen_by_repo.append(t_id) or [])
        ctx = _make_ctx(tenant_id=tenant_id, churn_predictor=_SpyPredictor())

        with patch("app.agents.customer.tools.TrainingDataRepository", return_value=mock_repo):
            await get_churn_risks(ctx)

        assert seen_by_predictor == [tenant_id]
        assert seen_by_repo == [tenant_id]
        assert other_tenant not in seen_by_predictor
        assert other_tenant not in seen_by_repo

    async def test_top_n_is_respected(self):
        customer_id = uuid.uuid4()

        class _SpyPredictor:
            def predict_risks(self, t_id, orders, *, as_of=None):
                return {str(customer_id): 0.9, str(uuid.uuid4()): 0.7, str(uuid.uuid4()): 0.5}

        mock_tr = AsyncMock()
        mock_tr.customer_orders.return_value = []

        mock_customer = MagicMock()
        mock_customer.display_name = "أبو علي"
        mock_customer.phone_number = "+96176000001"
        mock_cr = AsyncMock()
        mock_cr.get.return_value = mock_customer

        ctx = _make_ctx(churn_predictor=_SpyPredictor())
        with (
            patch("app.agents.customer.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.customer.tools.CustomerRepository", return_value=mock_cr),
        ):
            result = await get_churn_risks(ctx, top_n=1)

        assert len(result.customers) == 1

    async def test_customers_sorted_descending_by_score(self):
        cid_high = uuid.uuid4()
        cid_low = uuid.uuid4()

        class _SpyPredictor:
            def predict_risks(self, t_id, orders, *, as_of=None):
                # Return low first, expect them sorted high first
                return {str(cid_low): 0.3, str(cid_high): 0.9}

        mock_tr = AsyncMock()
        mock_tr.customer_orders.return_value = []

        def _get_customer(t_id, cid):
            m = MagicMock()
            m.display_name = "نموذج"
            m.phone_number = "+96176000001"
            return m

        mock_cr = AsyncMock()
        mock_cr.get.side_effect = _get_customer

        ctx = _make_ctx(churn_predictor=_SpyPredictor())
        with (
            patch("app.agents.customer.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.customer.tools.CustomerRepository", return_value=mock_cr),
        ):
            result = await get_churn_risks(ctx, top_n=2)

        assert result.customers[0].risk_score >= result.customers[-1].risk_score


# ── draft_reengagement ─────────────────────────────────────────────────────────


class TestDraftReengagement:
    async def test_returns_llm_message_when_successful(self):
        from app.agents.customer.schemas import DraftMessage

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = DraftMessage(message_ar="وينك يا أبو خالد؟")
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        customer = _make_customer_risk()
        result = await draft_reengagement(ctx, customer)

        assert result == "وينك يا أبو خالد؟"

    async def test_returns_fallback_on_llm_error(self):
        from prompts import customer_agent_ar

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = RuntimeError("provider down")
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        customer = _make_customer_risk()
        result = await draft_reengagement(ctx, customer)

        assert result == customer_agent_ar.FALLBACK_DRAFT.format(
            customer_name=customer.customer_name_ar
        )

    async def test_uses_customer_name_in_prompt(self):
        from app.agents.customer.schemas import DraftMessage

        captured: list = []

        async def _capture(messages, **_):
            captured.extend(messages)
            return DraftMessage(message_ar="تفضل")

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = _capture
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        ctx = _make_ctx(router=mock_router)
        customer = _make_customer_risk()
        await draft_reengagement(ctx, customer)

        prompt_text = captured[0].content
        assert customer.customer_name_ar in prompt_text

    async def test_no_name_uses_fallback_honorific(self):
        from app.agents.customer.schemas import DraftMessage

        captured: list = []

        async def _capture(messages, **_):
            captured.extend(messages)
            return DraftMessage(message_ar="مرحبا")

        mock_model = AsyncMock()
        mock_model.ainvoke.side_effect = _capture
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        customer = CustomerRisk(
            customer_id=uuid.uuid4(),
            customer_name_ar=None,  # no name
            phone_number="+96176000001",
            risk_score=0.9,
            last_order_at=None,
        )
        ctx = _make_ctx(router=mock_router)
        await draft_reengagement(ctx, customer)

        assert "حبيبنا" in captured[0].content


# ── queue_reengagement ─────────────────────────────────────────────────────────


class TestQueueReengagement:
    def _make_pending_record(self, customer: CustomerRisk, draft: str) -> MagicMock:
        record = MagicMock()
        record.id = uuid.uuid4()
        record.draft_message_ar = draft
        record.action_key = f"send_reengagement:tenant:{customer.customer_id}"
        return record

    async def test_creates_new_draft_when_none_exists(self):
        customer = _make_customer_risk()
        draft = "مرحبا يا أبو خالد"
        record = self._make_pending_record(customer, draft)

        mock_repo = AsyncMock()
        mock_repo.find_draft_for_customer.return_value = None
        mock_repo.create.return_value = record

        ctx = _make_ctx()
        with patch(
            "app.agents.customer.tools.PendingReengagementRepository", return_value=mock_repo
        ):
            result = await queue_reengagement(ctx, customer, draft)

        mock_repo.create.assert_called_once()
        assert result.reengagement_id == record.id
        assert result.draft_message_ar == draft

    async def test_idempotent_returns_existing_draft(self):
        """If a draft already exists for this customer, return it — no duplicate."""
        customer = _make_customer_risk()
        existing = self._make_pending_record(customer, "رسالة قديمة")

        mock_repo = AsyncMock()
        mock_repo.find_draft_for_customer.return_value = existing

        ctx = _make_ctx()
        with patch(
            "app.agents.customer.tools.PendingReengagementRepository", return_value=mock_repo
        ):
            result = await queue_reengagement(ctx, customer, "رسالة جديدة")

        mock_repo.create.assert_not_called()
        assert result.reengagement_id == existing.id
        assert result.draft_message_ar == existing.draft_message_ar

    async def test_action_key_encodes_tenant_and_customer(self):
        tenant_id = uuid.uuid4()
        customer = _make_customer_risk()
        record = self._make_pending_record(customer, "مرحبا")
        record.action_key = f"send_reengagement:{tenant_id}:{customer.customer_id}"

        mock_repo = AsyncMock()
        mock_repo.find_draft_for_customer.return_value = None
        mock_repo.create.return_value = record

        ctx = _make_ctx(tenant_id=tenant_id)
        with patch(
            "app.agents.customer.tools.PendingReengagementRepository", return_value=mock_repo
        ):
            result = await queue_reengagement(ctx, customer, "مرحبا")

        assert str(tenant_id) in result.action_key
        assert str(customer.customer_id) in result.action_key

    async def test_does_not_call_any_whatsapp_send(self):
        """Structural: queue_reengagement only writes a draft — no send path."""
        customer = _make_customer_risk()
        record = self._make_pending_record(customer, "مرحبا")

        mock_repo = AsyncMock()
        mock_repo.find_draft_for_customer.return_value = None
        mock_repo.create.return_value = record

        send_called = []
        ctx = _make_ctx()
        # Verify no "dispatch" or "send" method is called on the router or session
        ctx.router.send = MagicMock(side_effect=lambda *a, **k: send_called.append(1))
        ctx.session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with patch(
            "app.agents.customer.tools.PendingReengagementRepository", return_value=mock_repo
        ):
            await queue_reengagement(ctx, customer, "مرحبا")

        assert send_called == [], "queue_reengagement must not call any send path"


# ── _build_owner_reply ─────────────────────────────────────────────────────────


class TestBuildOwnerReply:
    def test_no_risks_returns_no_risks_message(self):
        from prompts import customer_agent_ar

        result = _build_owner_reply(ChurnRisks(customers=[], total_at_risk=0), None)
        assert result == customer_agent_ar.REPLY_NO_RISKS

    def test_no_risks_from_none_input(self):
        from prompts import customer_agent_ar

        result = _build_owner_reply(None, None)
        assert result == customer_agent_ar.REPLY_NO_RISKS

    def test_risks_found_includes_header_and_customer_line(self):

        customer = _make_customer_risk(risk_score=0.8)
        risks = ChurnRisks(customers=[customer], total_at_risk=1)
        result = _build_owner_reply(risks, None)

        assert "1" in result
        assert customer.customer_name_ar in result

    def test_queued_action_appended_to_reply(self):
        from prompts import customer_agent_ar

        customer = _make_customer_risk()
        risks = ChurnRisks(customers=[customer], total_at_risk=1)
        action = QueuedAction(
            reengagement_id=uuid.uuid4(),
            customer_id=customer.customer_id,
            customer_name_ar=customer.customer_name_ar,
            draft_message_ar="رسالة",
            action_key="send_reengagement:x:y",
        )
        result = _build_owner_reply(risks, action)

        assert (
            customer_agent_ar.REPLY_ACTION_QUEUED.format(customer_name=customer.customer_name_ar)
            in result
        )


# ── CustomerAgent.handle smoke tests ──────────────────────────────────────────


class TestCustomerAgent:
    def _make_sessionmaker(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_sessionmaker = MagicMock()
        mock_sessionmaker.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessionmaker.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_sessionmaker, mock_session

    async def test_no_at_risk_customers_returns_no_risks_reply(self):
        from app.agents.customer.agent import CustomerAgent
        from prompts import customer_agent_ar

        mock_sessionmaker, _ = self._make_sessionmaker()
        mock_tr = AsyncMock()
        mock_tr.customer_orders.return_value = []

        with patch("app.agents.customer.tools.TrainingDataRepository", return_value=mock_tr):
            agent = CustomerAgent(
                router=MagicMock(),
                settings=MagicMock(llm_max_retries=0),
                sessionmaker=mock_sessionmaker,
            )
            reply = await agent.handle("زبائني شو أخبارهم؟", uuid.uuid4())

        assert reply == customer_agent_ar.REPLY_NO_RISKS

    async def test_at_risk_path_returns_string_and_commits(self):
        """When a draft is queued, the session is committed."""
        from app.agents.customer.agent import CustomerAgent
        from app.agents.customer.schemas import DraftMessage

        mock_sessionmaker, mock_session = self._make_sessionmaker()

        customer_id = uuid.uuid4()

        class _SpyPredictor:
            def predict_risks(self, t_id, orders, *, as_of=None):
                return {str(customer_id): 0.9}

        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = DraftMessage(message_ar="وينك؟")
        mock_router = MagicMock()
        mock_router.tier1.return_value.with_structured_output.return_value = mock_model

        mock_customer = MagicMock()
        mock_customer.display_name = "أبو علي"
        mock_customer.phone_number = "+96176000001"

        mock_tr = AsyncMock()
        mock_tr.customer_orders.return_value = []
        mock_cr = AsyncMock()
        mock_cr.get.return_value = mock_customer

        record = MagicMock()
        record.id = uuid.uuid4()
        record.draft_message_ar = "وينك؟"
        record.action_key = f"send_reengagement:x:{customer_id}"
        mock_repo = AsyncMock()
        mock_repo.find_draft_for_customer.return_value = None
        mock_repo.create.return_value = record

        with (
            patch("app.agents.customer.tools.TrainingDataRepository", return_value=mock_tr),
            patch("app.agents.customer.tools.CustomerRepository", return_value=mock_cr),
            patch(
                "app.agents.customer.tools.PendingReengagementRepository", return_value=mock_repo
            ),
        ):
            agent = CustomerAgent(
                router=mock_router,
                settings=MagicMock(llm_max_retries=1),
                sessionmaker=mock_sessionmaker,
                churn_predictor=_SpyPredictor(),
            )
            reply = await agent.handle("زبائني شو أخبارهم؟", uuid.uuid4())

        assert isinstance(reply, str)
        assert len(reply) > 0
        mock_session.commit.assert_called_once()
