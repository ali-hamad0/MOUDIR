import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.inventory.agent import InventoryAgent
from app.agents.llm.router import GeminiRouter
from app.agents.order.agent import OrderAgent
from app.api import (
    activation,
    admin,
    approvals,
    auth,
    customers,
    inventory,
    me,
    orders,
    owners,
    profile,
    signup_requests,
    webhooks,
)
from app.db.session import create_engine
from app.infra.logging import configure_logging, get_logger
from app.infra.settings import Settings, get_settings
from app.infra.supplier_dispatch import SupplierDispatcher
from app.infra.vault import resolve_secrets
from app.services.dispatcher import MessageDispatcher


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

    app.state.db_engine = create_engine()
    log.info("modir.db.engine.created")

    # LLM router + OrderAgent + dispatcher are built ONCE here (constitution IV:
    # agents/models load once, served via app.state — never per request). The
    # agent opens its own session per message from this sessionmaker.
    _configure_langsmith(settings)
    sessionmaker = async_sessionmaker(
        app.state.db_engine, class_=AsyncSession, expire_on_commit=False
    )
    app.state.llm_router = GeminiRouter(settings)
    app.state.order_agent = OrderAgent(app.state.llm_router, settings, sessionmaker)
    # The InventoryAgent mirrors the OrderAgent: built once, opens its own session
    # per call. Task 4.9 reaches it via app.state.inventory_agent to draft a reorder
    # PO inline when order completion drops stock below threshold.
    app.state.inventory_agent = InventoryAgent(app.state.llm_router, settings, sessionmaker)
    app.state.dispatcher = MessageDispatcher(app.state.order_agent, sessionmaker)
    # The supplier dispatcher is built once here (like EmailSender / the agents):
    # the approvals API (Task 4.12) fires its `dispatch` as a background task after
    # an approve commits. It opens its OWN session per call from this sessionmaker,
    # and it sends ONLY behind a valid signed token (ActionGate) — constitution V.
    app.state.supplier_dispatcher = SupplierDispatcher(settings, sessionmaker)
    log.info("modir.agents.ready", langsmith=settings.langsmith_tracing)

    # Future: app.state.demand_model = joblib.load(...)

    yield

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
    app.include_router(customers.router)
    app.include_router(me.router)
    app.include_router(webhooks.router)

    return app


app = create_app()
