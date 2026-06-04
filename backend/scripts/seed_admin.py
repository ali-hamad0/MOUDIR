"""Seed the first Modir founder / super-admin (Phase 1.5, Task 3.14).

Idempotent: if an admin with FOUNDER_EMAIL already exists, it does nothing.

DEV ONLY. The password below is a known development credential so you can log
into /admin/login immediately. Rotate it (or recreate the admin with a real
secret) before any non-development use — never ship this password.

Run on the host (override the Docker hostnames in .env to localhost):
    cd backend
    DATABASE_URL=postgresql+asyncpg://modir:modir@127.0.0.1:5432/modir \
    REDIS_URL=redis://127.0.0.1:6379/0 VAULT_ADDR=http://127.0.0.1:8200 \
    VAULT_TOKEN=root uv run python -m scripts.seed_admin

Or inside the running api container:
    docker compose exec api python -m scripts.seed_admin
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Admin
from app.db.session import create_engine
from app.infra.logging import get_logger
from app.infra.security import hash_password
from app.repositories.admins import AdminRepository

log = get_logger("seed_admin")

FOUNDER_EMAIL = "alimufidhamad0000@gmail.com"
FOUNDER_DEV_PASSWORD = "founder-dev-pass"  # DEV ONLY — rotate before real use.


async def seed() -> None:
    engine = create_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            repo = AdminRepository(session)
            existing = await repo.get_by_email(FOUNDER_EMAIL)
            if existing is not None:
                log.info("seed_admin.exists", email=FOUNDER_EMAIL)
                return
            await repo.add(
                Admin(
                    email=FOUNDER_EMAIL,
                    hashed_password=hash_password(FOUNDER_DEV_PASSWORD),
                    is_active=True,
                )
            )
            await session.commit()
            log.info("seed_admin.created", email=FOUNDER_EMAIL)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
