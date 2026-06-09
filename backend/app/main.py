import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.advisor.agent import AdvisorAgent
from app.agents.customer.agent import CustomerAgent
from app.agents.finance.agent import FinanceAgent
from app.agents.inventory.agent import InventoryAgent
from app.agents.llm.router import FallbackLLMRouter, GeminiRouter, LLMRouter
from app.agents.ocr.agent import BillExtractionAgent
from app.agents.order.agent import OrderAgent
from app.agents.supervisor.agent import OwnerSupervisor
from app.api import (
    activation,
    admin,
    approvals,
    auth,
    bills,
    chat,
    customers,
    inventory,
    me,
    orders,
    owners,
    predictions,
    profile,
    signup_requests,
    webhooks,
)
from app.db.session import create_engine
from app.infra.bill_committer import BillCommitter
from app.infra.checkpointer import build_checkpointer
from app.infra.embeddings import build_embedding_client
from app.infra.logging import configure_logging, get_logger
from app.infra.ocr import build_ocr_engine
from app.infra.rate_limiter import RateLimiter
from app.infra.settings import Settings, get_settings
from app.infra.storage import StorageClient
from app.infra.supplier_dispatch import SupplierDispatcher
from app.infra.vault import resolve_secrets
from app.ml.predictors import (
    build_anomaly_detector,
    build_churn_predictor,
    build_demand_predictor,
)
from app.services.dispatcher import MessageDispatcher


def _build_llm_router(settings: Settings, log) -> LLMRouter:
    """Build the LLM router, adding optional fallback providers when keyed.

    Gemini is always included (required). Grok and Anthropic are added only when
    their keys are non-empty (resolved from Vault — absent keys are logged as info
    at startup and those providers are skipped, not crashed). The result is a
    FallbackLLMRouter when multiple providers are available, or just GeminiRouter
    when only Gemini is configured (avoids the with_fallbacks wrapper overhead).
    """
    providers: list[LLMRouter] = [GeminiRouter(settings)]

    if settings.grok_api_key.get_secret_value():
        from app.agents.llm.grok_router import GrokRouter

        providers.append(GrokRouter(settings))
        log.info("modir.llm.grok.enabled", model_t1=settings.grok_tier1_model)
    else:
        log.info("modir.llm.grok.skipped", reason="no_api_key")

    if settings.anthropic_api_key.get_secret_value():
        from app.agents.llm.anthropic_router import AnthropicRouter

        providers.append(AnthropicRouter(settings))
        log.info("modir.llm.anthropic.enabled", model_t1=settings.anthropic_tier1_model)
    else:
        log.info("modir.llm.anthropic.skipped", reason="no_api_key")

    if len(providers) == 1:
        return providers[0]
    router = FallbackLLMRouter(providers)
    log.info("modir.llm.fallback_chain.ready", provider_count=len(providers))
    return router


def _configure_langsmith(settings: Settings) -> None:
    """Point LangChain/LangSmith at our project using the Vault-resolved key.

    LangChain reads these from the environment itself, so this is the one place
    we set os.environ — from a Vault secret, never a literal. (The forbidden-
    pattern CI gate targets os.getenv, which we still never use.)
    """
    if not settings.langsmith_tracing:
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan. Startup before yield, shutdown after.

    Singletons live here:
    - Database engine (Task 0.6)
    - ML models (Phase 6)
    - LLM router (Phase 7)
    - Vector store connection (Phase 5)
    """
    configure_logging()
    log = get_logger("lifespan")
    settings = get_settings()

    log.info(
        "modir.startup",
        environment=settings.environment,
        log_level=settings.log_level,
    )

    settings = resolve_secrets(settings)
    log.info("modir.vault.connected")

    # Redis client (Phase 8) — used for per-tenant rate limiting. Built once here
    # and stored on app.state so the rate_limit_check dependency can reach it per
    # request without reopening a connection. The RateLimiter is stateless; it
    # just needs the client and the configured default RPM.
    from redis.asyncio import Redis as AsyncRedis

    app.state.redis = AsyncRedis.from_url(
        str(settings.redis_url), encoding="utf-8", decode_responses=True
    )
    app.state.rate_limiter = RateLimiter(app.state.redis, settings.rate_limit_default_rpm)
    log.info("modir.rate_limiter.ready", default_rpm=settings.rate_limit_default_rpm)

    app.state.db_engine = create_engine()
    log.info("modir.db.engine.created")

    # LLM router + OrderAgent + dispatcher are built ONCE here (constitution IV:
    # agents/models load once, served via app.state — never per request). The
    # agent opens its own session per message from this sessionmaker.
    _configure_langsmith(settings)
    sessionmaker = async_sessionmaker(
        app.state.db_engine, class_=AsyncSession, expire_on_commit=False
    )
    app.state.llm_router = _build_llm_router(settings, log)
    # Embedding client (Phase 5 RAG). Provider-agnostic, built once; `stub` for dev/CI
    # (offline), `gemini` for the real embedder (keyed by the existing Vault
    # gemini_api_key). Built before the OrderAgent so its search_knowledge_base tool
    # (Task 5.16) can use it; the worker (Task 5.15) also embeds KB docs + bills.
    app.state.embedding_client = build_embedding_client(settings)
    app.state.order_agent = OrderAgent(
        app.state.llm_router, settings, sessionmaker, app.state.embedding_client
    )
    # Demand predictor (Phase 6, Task 6.6). Loaded ONCE here (Constitution IV.5:
    # joblib.load lives in lifespan, NEVER in a route) and served via app.state DI.
    # The factory returns the trained predictor when ml_mode="trained" and the artifact
    # + card are present, else the offline stub — the CI/dev default, exactly like
    # ocr_mode/embedding_mode. A missing artifact degrades to the stub (logged), never
    # crashing startup. Built before the InventoryAgent so it can be injected into it.
    app.state.demand_predictor = build_demand_predictor(settings)
    # Churn + anomaly predictors are loaded the SAME way (once, here) and served by the
    # read-only /predictions/* API (Task 6.10) via DI — never loaded in a route. Same
    # stub-by-default / trained-when-artifact-present contract as the demand predictor.
    app.state.churn_predictor = build_churn_predictor(settings)
    app.state.anomaly_detector = build_anomaly_detector(settings)
    log.info("modir.ml.predictors.ready", ml_mode=settings.ml_mode)
    # LangGraph Postgres checkpointer (Task 7.2). Persists supervisor conversation
    # state so a mid-flight owner session resumes after a process restart. The pool
    # is closed in the shutdown block below. LangGraph owns its own tables (created
    # by checkpointer.setup()) — they are NOT in our Alembic migrations.
    app.state.checkpointer, _checkpoint_pool = await build_checkpointer(str(settings.database_url))
    log.info("modir.checkpointer.ready")
    # The InventoryAgent mirrors the OrderAgent: built once, opens its own session
    # per call. Task 4.9 reaches it via app.state.inventory_agent to draft a reorder
    # PO inline when order completion drops stock below threshold. Task 6.7 hands it the
    # demand predictor so forecast_demand's reorder quantity comes from the trained model.
    app.state.inventory_agent = InventoryAgent(
        app.state.llm_router, settings, sessionmaker, app.state.demand_predictor
    )
    # The FinanceAgent mirrors the InventoryAgent: built once, opens its own session
    # per call. The supervisor (Task 7.6) will route finance questions to it via
    # app.state.finance_agent. Read-only: no HIL gate. The anomaly_detector is the
    # same lifespan singleton used by the /predictions/* API (Task 6.10).
    app.state.finance_agent = FinanceAgent(
        app.state.llm_router, settings, sessionmaker, app.state.anomaly_detector
    )
    # The AdvisorAgent is read-only: no HIL, no commit. It synthesizes all three Phase 6
    # ML predictors + live DB reads into a Lebanese Arabic morning briefing (Task 7.5).
    # Constitution IV: ML models produce the numbers; Tier 2 LLM only explains them.
    app.state.advisor_agent = AdvisorAgent(
        app.state.llm_router,
        settings,
        sessionmaker,
        demand_predictor=app.state.demand_predictor,
        churn_predictor=app.state.churn_predictor,
        anomaly_detector=app.state.anomaly_detector,
    )
    # The CustomerAgent mirrors the FinanceAgent: built once, opens its own session
    # per call. Constitution V: queue_reengagement writes a draft for HIL approval —
    # there is NO send path in Phase 7. Phase 10 (Meta API) adds it.
    app.state.customer_agent = CustomerAgent(
        app.state.llm_router, settings, sessionmaker, app.state.churn_predictor
    )
    # The BillExtractionAgent mirrors the other agents: built once, opens its own
    # session per call. The OCR worker (Task 5.8) reaches it via
    # app.state.bill_agent to structure an uploaded bill's OCR text into a BillData.
    app.state.bill_agent = BillExtractionAgent(app.state.llm_router, settings, sessionmaker)
    # The OwnerSupervisor wires all five specialist agents under one LangGraph
    # (Task 7.6). Built AFTER all agents are ready; attached to the Postgres
    # checkpointer so conversation state survives container restarts (AD-7.2).
    app.state.supervisor = OwnerSupervisor(
        router=app.state.llm_router,
        order_agent=app.state.order_agent,
        inventory_agent=app.state.inventory_agent,
        finance_agent=app.state.finance_agent,
        customer_agent=app.state.customer_agent,
        advisor_agent=app.state.advisor_agent,
        checkpointer=app.state.checkpointer,
        sessionmaker=sessionmaker,
    )
    log.info("modir.supervisor.ready")
    app.state.dispatcher = MessageDispatcher(
        app.state.order_agent, sessionmaker, supervisor=app.state.supervisor
    )
    # The supplier dispatcher is built once here (like EmailSender / the agents):
    # the approvals API (Task 4.12) fires its `dispatch` as a background task after
    # an approve commits. It opens its OWN session per call from this sessionmaker,
    # and it sends ONLY behind a valid signed token (ActionGate) — constitution V.
    app.state.supplier_dispatcher = SupplierDispatcher(settings, sessionmaker)
    # The bill committer applies an approved OCR'd bill to stock (Phase 5). Built
    # once here like the supplier dispatcher; the bill-review API (Task 5.12) fires
    # its `commit` as a background task after an approve commits. It opens its OWN
    # session per call and applies stock ONLY behind a valid signed bill.commit token
    # (the SAME ActionGate as PO dispatch — a new action string, not a new gate).
    app.state.bill_committer = BillCommitter(settings, sessionmaker)
    # Object storage for supplier-bill images (Phase 5). Built once here like the
    # other infra clients; the bill bucket is ensured at startup (idempotent). The
    # upload API streams to it and the OCR worker fetches from it — always under
    # tenant-prefixed keys (the Wall for storage).
    app.state.storage = StorageClient(settings)
    await app.state.storage.ensure_bucket()
    # OCR engine (Phase 5). Provider-agnostic, built once here and selected by
    # ocr_mode: `stub` for dev/CI (offline), `cloud_vision` for the real engine. The
    # worker (Task 5.8) uses it to turn a bill image into text; the LLM only
    # structures that text (constitution IV). The provider SDK stays confined to
    # app/infra/ocr/cloud_vision.py.
    app.state.ocr_engine = build_ocr_engine(settings)
    log.info(
        "modir.agents.ready",
        langsmith=settings.langsmith_tracing,
        ocr_mode=settings.ocr_mode,
        embedding_mode=settings.embedding_mode,
        ml_mode=settings.ml_mode,
        finance_agent="ready",
        customer_agent="ready",
        advisor_agent="ready",
    )

    yield

    await app.state.redis.aclose()
    log.info("modir.redis.closed")
    await _checkpoint_pool.close()
    log.info("modir.checkpointer.closed")
    await app.state.db_engine.dispose()
    log.info("modir.db.engine.disposed")

    log.info("modir.shutdown")


def create_app() -> FastAPI:
    """Application factory. Used by tests to build isolated app instances."""
    app = FastAPI(
        title="Modir API",
        description="AI business operations assistant for Lebanese SMEs",
        version="0.1.0",
        lifespan=lifespan,
    )

    # The dashboard (Phase 3) is a separate origin; allow it explicitly. Origins
    # come from typed Settings, never "*" with credentials. Methods/headers are
    # limited to what the dashboard actually uses (auth header + JSON bodies).
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    async def health():
        """Liveness probe. Used by Docker healthcheck and load balancers."""
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(signup_requests.router)
    app.include_router(activation.router)
    app.include_router(owners.router)
    app.include_router(profile.router)
    app.include_router(orders.router)
    app.include_router(inventory.router)
    app.include_router(approvals.router)
    app.include_router(bills.router)
    app.include_router(customers.router)
    app.include_router(me.router)
    app.include_router(predictions.router)
    app.include_router(chat.router)
    app.include_router(webhooks.router)

    return app


app = create_app()
