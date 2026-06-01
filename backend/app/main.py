from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.infra.logging import configure_logging, get_logger
from app.infra.settings import get_settings


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

    # Future: app.state.db_engine = create_async_engine(...)
    # Future: app.state.demand_model = joblib.load(...)
    # Future: app.state.llm_router = LLMRouter(settings)

    yield

    log.info("modir.shutdown")
    # Future: await app.state.db_engine.dispose()


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

    return app


app = create_app()
