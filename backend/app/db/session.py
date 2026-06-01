from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infra.settings import get_settings


def create_engine():
    """Called once from lifespan handler."""
    settings = get_settings()
    return create_async_engine(
        str(settings.database_url),
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields a session, closes it on request end.

    Usage in routes:
        async def my_route(db: AsyncSession = Depends(get_db_session)):
    """
    engine = request.app.state.db_engine
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
