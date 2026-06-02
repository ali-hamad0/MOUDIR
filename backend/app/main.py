from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, owners, profile, webhooks
from app.db.session import create_engine
from app.infra.logging import configure_logging, get_logger
from app.infra.settings import get_settings
from app.infra.vault import resolve_secrets


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

    # Future: app.state.demand_model = joblib.load(...)
    # Future: app.state.llm_router = LLMRouter(settings)

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

    @app.get("/health")
    async def health():
        """Liveness probe. Used by Docker healthcheck and load balancers."""
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(owners.router)
    app.include_router(profile.router)
    app.include_router(webhooks.router)

    return app


app = create_app()
