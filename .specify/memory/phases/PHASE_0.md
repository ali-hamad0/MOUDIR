# Phase 0 — Foundation & Setup

> **Hand this file to Claude Code in VS Code with:**
> "Read `.specify/memory/constitution.md` and this file. Implement Phase 0 task by task. Pause for approval after each task before committing."

---

## Goal

A clean repo where `docker compose up` brings up an empty but working FastAPI
service with structured logging, secrets in Vault, and a migration that runs
once. By the end of this phase, nothing does anything useful yet — but every
foundation is solid enough that Phases 1–9 won't need to fix it later.

## Prerequisites

- [ ] `constitution.md` exists at `.specify/memory/constitution.md`
- [ ] `uv` installed (`curl -Ls https://astral.sh/uv/install.sh | sh`)
- [ ] Docker Desktop running
- [ ] Python 3.11+ available
- [ ] Empty git repository initialized

## Phase 0 — Tasks Overview

| Task | What | Branch |
|------|------|--------|
| 0.1 | Repository skeleton + layer structure | `chore/MOD-0-repo-skeleton` |
| 0.2 | `pyproject.toml` with uv | `chore/MOD-0-pyproject-uv` |
| 0.3 | Settings class with pydantic-settings | `feature/MOD-0-settings` |
| 0.4 | Structured logging with structlog | `feature/MOD-0-logging` |
| 0.5 | FastAPI skeleton with lifespan | `feature/MOD-0-fastapi-skeleton` |
| 0.6 | Database + Alembic baseline migration | `feature/MOD-0-database` |
| 0.7 | Vault integration for secrets | `feature/MOD-0-vault` |
| 0.8 | Dockerfile for API | `chore/MOD-0-api-dockerfile` |
| 0.9 | docker-compose.yml — full stack | `chore/MOD-0-compose` |
| 0.10 | GitHub Actions CI | `chore/MOD-0-ci` |
| 0.11 | pre-commit hooks (ruff, black) | `chore/MOD-0-precommit` |

Each task is a separate branch and PR. No exceptions.

---

## Task 0.1 — Repository Skeleton

**Branch:** `chore/MOD-0-repo-skeleton`

Create the directory structure exactly as the constitution specifies. Empty
folders get a `.gitkeep` file so git tracks them.

```
modir/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── api/
│   │   │   └── __init__.py
│   │   ├── services/
│   │   │   └── __init__.py
│   │   ├── repositories/
│   │   │   └── __init__.py
│   │   ├── domain/
│   │   │   └── __init__.py
│   │   ├── agents/
│   │   │   └── __init__.py
│   │   ├── ml/
│   │   │   └── __init__.py
│   │   ├── ocr/
│   │   │   └── __init__.py
│   │   ├── infra/
│   │   │   └── __init__.py
│   │   └── db/
│   │       ├── __init__.py
│   │       └── models.py    (empty for now)
│   ├── tests/
│   │   └── __init__.py
│   ├── alembic/
│   │   └── .gitkeep
│   ├── prompts/
│   │   └── .gitkeep
│   ├── scripts/
│   │   └── seed_vault.sh    (Task 0.7 — seeds Vault dev mode with placeholder secrets)
│   └── Dockerfile           (Task 0.8)
├── frontend/
│   └── .gitkeep             (Phase 3 fills this)
├── .github/
│   └── workflows/
│       └── ci.yml           (Task 0.10)
├── .specify/
│   └── memory/
│       └── constitution.md  (already here)
├── docker-compose.yml       (Task 0.9)
├── .env.example             (Task 0.3)
├── .gitignore
├── .pre-commit-config.yaml  (Task 0.11)
├── README.md
└── pyproject.toml           (Task 0.2)
```

**`.gitignore` contents:**
```
__pycache__/
*.pyc
.venv/
.env
.env.local
*.db
*.log
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/
node_modules/
dist/
build/
.DS_Store
```

**`README.md` minimal contents:**
```markdown
# Modir
AI Business Operations Assistant for Lebanese SMEs.
See `.specify/memory/constitution.md` for engineering principles.
See `ROADMAP.md` for the build plan.

## Quick start
\`\`\`bash
cp .env.example .env
docker compose up
\`\`\`
```

**Commit message:**
```
chore(setup): create repo skeleton with layered structure

Layers per constitution section 3: api / services / repositories /
domain / agents / ml / ocr / infra / db.
```

**Verification:**
- `tree -L 3 -I '__pycache__|.git'` shows the structure above
- Every Python module folder has `__init__.py`

---

## Task 0.2 — pyproject.toml with uv

**Branch:** `chore/MOD-0-pyproject-uv`

Use `uv` exclusively. Never `pip install` directly.

**`pyproject.toml`:**
```toml
[project]
name = "modir-backend"
version = "0.1.0"
description = "AI business operations assistant for Lebanese SMEs"
requires-python = ">=3.11"
dependencies = [
    "fastapi[standard]>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.6.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "pgvector>=0.3.0",        # Phase 1+ — vector similarity search
    "structlog>=24.4.0",
    "httpx>=0.28.0",
    "hvac>=2.3.0",
    "tenacity>=9.0.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.8.0",
    "black>=24.10.0",
    "mypy>=1.13.0",
    "httpx>=0.28.0",
    "pre-commit>=4.0.0",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "ASYNC"]
ignore = ["E501"]

[tool.black]
line-length = 100
target-version = ["py311"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["backend/tests"]
```

**Commands to run:**
```bash
cd backend
uv sync
uv run pytest --version   # verify it works
```

**Commit message:**
```
chore(deps): pin dependencies via uv

Pin all versions. No pip. uv.lock is committed.
```

**Verification:**
- `uv.lock` exists and is committed
- `uv run python -c "import fastapi, sqlalchemy, structlog"` succeeds

---

## Task 0.3 — Settings Class

**Branch:** `feature/MOD-0-settings`

One Settings class. Zero `os.getenv()` calls anywhere else in the codebase.
If a required key is missing at startup, the app refuses to start.

**File: `backend/app/infra/settings.py`**
```python
from functools import lru_cache
from pathlib import Path
from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. All values typed and validated at startup.

    Required env vars: DATABASE_URL, REDIS_URL, VAULT_ADDR, VAULT_TOKEN
    """

    # Environment
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # Database
    database_url: PostgresDsn

    # Redis
    redis_url: RedisDsn

    # Vault (secrets resolution)
    vault_addr: str
    vault_token: SecretStr

    # MinIO (later phases)
    minio_endpoint: str = Field(default="minio:9000")
    minio_access_key: SecretStr = Field(default=SecretStr("changeme"))
    minio_secret_key: SecretStr = Field(default=SecretStr("changeme"))

    # LLM provider keys — RESOLVED FROM VAULT, not env. Placeholder here.
    gemini_api_key: SecretStr = Field(default=SecretStr("from-vault"))

    # Paths
    base_dir: Path = Field(default=Path(__file__).parent.parent.parent)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )


@lru_cache
def get_settings() -> Settings:
    """Singleton accessor. Used via FastAPI Depends() in routes."""
    return Settings()
```

**File: `.env.example`**
```
ENVIRONMENT=development
LOG_LEVEL=INFO

DATABASE_URL=postgresql+asyncpg://modir:modir@db:5432/modir
REDIS_URL=redis://redis:6379/0

VAULT_ADDR=http://vault:8200
VAULT_TOKEN=root

MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=modir-access
MINIO_SECRET_KEY=modir-secret-changeme
```

**Commit message:**
```
feat(settings): add pydantic-settings config class

Single source of truth for configuration. App refuses to start if
required keys are missing.
```

**Verification:**
- `uv run python -c "from app.infra.settings import get_settings; print(get_settings())"` fails with a clear error if `.env` is missing required keys
- `grep -r "os.getenv" backend/app/` returns nothing

---

## Task 0.4 — Structured Logging

**Branch:** `feature/MOD-0-logging`

No `print()`. Ever. `structlog` configured for JSON output, written to file
and stdout. Logs survive container restarts via a mounted volume.

**File: `backend/app/infra/logging.py`**
```python
import logging
import sys
from pathlib import Path
import structlog
from app.infra.settings import get_settings


def configure_logging() -> None:
    """Configure structlog for JSON output. Call once at app startup."""
    settings = get_settings()

    log_dir = Path("/var/log/modir")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "modir.json.log"

    # Standard library logging — file + stdout
    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )

    # Structlog processors — JSON output with timestamps
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a logger. Use this instead of logging.getLogger."""
    return structlog.get_logger(name)
```

**Commit message:**
```
feat(logging): configure structlog for JSON output

All logs go to /var/log/modir/modir.json.log AND stdout.
File is mounted from the host so logs survive container restarts.
```

**Verification:**
- `grep -rn "print(" backend/app/` returns nothing
- A `get_logger(__name__).info("test", key="value")` call produces a single JSON line

---

## Task 0.5 — FastAPI Skeleton with Lifespan

**Branch:** `feature/MOD-0-fastapi-skeleton`

The `lifespan` handler is where singletons live (per constitution section 6).
For now it's mostly empty — but the structure is in place for Phase 6 to add
ML models without rewriting anything.

**File: `backend/app/main.py`**
```python
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
```

**Commit message:**
```
feat(api): FastAPI app with lifespan handler

Lifespan is where singletons will live per constitution section 6.
/health endpoint added for Docker healthchecks.
```

**Verification:**
- `uv run uvicorn app.main:app --reload` starts the server
- `curl localhost:8000/health` returns `{"status":"ok"}`
- The startup log line is valid JSON

---

## Task 0.6 — Database + Alembic Baseline

**Branch:** `feature/MOD-0-database`

SQLAlchemy 2.x async session. Alembic for migrations. Base ORM class lives
in `db/models.py` and is the only place repositories import from.

**File: `backend/app/db/models.py`**
```python
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base ORM class. Every model inherits from this."""

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

**File: `backend/app/db/session.py`**
```python
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from fastapi import Request
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
```

**Then update `app/main.py` lifespan:**
```python
# Inside lifespan, before yield:
app.state.db_engine = create_engine()
log.info("modir.db.engine.created")

# After yield:
await app.state.db_engine.dispose()
log.info("modir.db.engine.disposed")
```

**Alembic init:**
```bash
cd backend
uv run alembic init -t async alembic
```

**Edit `backend/alembic/env.py`** — point at `Base.metadata` and use the async URL from settings. (Claude Code can do this — instruct it to read the standard async template and wire it to `app.db.models.Base`.)

**Create baseline migration:**
```bash
uv run alembic revision --autogenerate -m "baseline"
```

For Phase 0 this generates an empty migration (no models yet). That's fine.
Phase 1 will add the `tenants` and `users` tables.

**Commit message:**
```
feat(db): SQLAlchemy 2.x async + Alembic baseline

Base ORM class with UUID id and timestamp columns.
Engine created in lifespan, disposed on shutdown.
Sessions yielded via FastAPI Depends.
```

**Verification:**
- `uv run alembic upgrade head` runs without error against a running Postgres
- The startup log shows `modir.db.engine.created`

---

## Task 0.7 — Vault Integration

**Branch:** `feature/MOD-0-vault`

All secrets resolve from Vault at startup. `.env` only holds non-secret config
and the Vault address + token. After startup, no code reads from env vars
for secrets.

**File: `backend/app/infra/vault.py`**
```python
import hvac
from app.infra.settings import get_settings
from app.infra.logging import get_logger

log = get_logger(__name__)


class VaultClient:
    """Reads secrets from Vault KV v2. Used at app startup."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = hvac.Client(
            url=settings.vault_addr,
            token=settings.vault_token.get_secret_value(),
        )
        if not self._client.is_authenticated():
            raise RuntimeError("Vault authentication failed at startup")

    def read_secret(self, path: str, key: str) -> str:
        """Read a single key from a secret at the given path.

        path: 'modir/gemini' (without 'secret/data/' prefix)
        key: the field name inside the secret
        """
        try:
            response = self._client.secrets.kv.v2.read_secret_version(path=path)
            return response["data"]["data"][key]
        except Exception as e:
            log.error("vault.read.failed", path=path, key=key, error=str(e))
            raise


def resolve_secrets(settings):
    """Mutate the settings object with secrets fetched from Vault.

    Called from lifespan startup. Refuses to proceed if any secret is missing.
    """
    vault = VaultClient()
    secrets_map = {
        "gemini_api_key": ("modir/llm", "gemini_api_key"),
        "minio_access_key": ("modir/minio", "access_key"),
        "minio_secret_key": ("modir/minio", "secret_key"),
    }
    for field, (path, key) in secrets_map.items():
        from pydantic import SecretStr
        value = vault.read_secret(path, key)
        setattr(settings, field, SecretStr(value))
    log.info("vault.secrets.resolved", count=len(secrets_map))
    return settings
```

**In `app/main.py` lifespan, BEFORE creating the engine:**
```python
from app.infra.vault import resolve_secrets

# Inside lifespan, after configure_logging():
settings = resolve_secrets(get_settings())
log.info("modir.vault.connected")
```

**Vault seeding script (`backend/scripts/seed_vault.sh`):**
```bash
#!/bin/bash
# Seeds Vault dev mode with placeholder secrets.
# Run this once after `docker compose up` brings vault online.
set -e

export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=root

vault kv put secret/modir/llm gemini_api_key="placeholder-rotate-before-prod"
vault kv put secret/modir/minio access_key="modir-access" secret_key="modir-secret-changeme"

echo "Vault seeded with placeholder secrets."
echo "Replace placeholders before production!"
```

**Commit message:**
```
feat(vault): resolve secrets from Vault at startup

App refuses to boot if Vault is unreachable or required secrets are missing.
Seed script provided for dev mode.
```

**Verification:**
- `grep -ri "api_key" backend/app/ | grep -v "vault\|settings"` returns nothing
- Killing Vault and restarting `api` produces a clear error and refuses to start

---

## Task 0.8 — Dockerfile for API

**Branch:** `chore/MOD-0-api-dockerfile`

**File: `backend/Dockerfile`**
```dockerfile
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN mkdir -p /var/log/modir

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Commit message:**
```
chore(docker): API Dockerfile with uv and healthcheck

Multi-stage build with uv for fast, reproducible installs.
Healthcheck wired to /health endpoint.
```

**Verification:**
- `docker build -t modir-api ./backend` succeeds
- Image size is reasonable (under 500MB)

---

## Task 0.9 — docker-compose.yml (The Full Stack)

**Branch:** `chore/MOD-0-compose`

This is the most important file in the project. Every service. Every volume.
Every network. Be prepared to defend every line in code review.

**File: `docker-compose.yml`**
```yaml
services:
  db:
    image: pgvector/pgvector:pg16   # NOT postgres:16 — pgvector extension needed from Phase 1
    environment:
      POSTGRES_USER: modir
      POSTGRES_PASSWORD: modir
      POSTGRES_DB: modir
    volumes:
      - db_data:/var/lib/postgresql/data
    networks:
      - modir_net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U modir"]
      interval: 5s
      timeout: 3s
      retries: 5
    # No ports exposed externally — db is internal only.

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    networks:
      - modir_net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: modir-access
      MINIO_ROOT_PASSWORD: modir-secret-changeme
    volumes:
      - minio_data:/data
    networks:
      - modir_net
    ports:
      - "9001:9001"  # console only; API stays internal
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5

  vault:
    image: hashicorp/vault:latest
    environment:
      VAULT_DEV_ROOT_TOKEN_ID: root
      VAULT_DEV_LISTEN_ADDRESS: 0.0.0.0:8200
    cap_add:
      - IPC_LOCK
    volumes:
      - vault_data:/vault/data
    networks:
      - modir_net
    ports:
      - "8200:8200"  # exposed so seed script can hit it from host

  migrate:
    build:
      context: ./backend
    env_file: .env
    networks:
      - modir_net
    depends_on:
      db:
        condition: service_healthy
      vault:
        condition: service_started
    command: ["uv", "run", "alembic", "upgrade", "head"]
    restart: "no"

  api:
    build:
      context: ./backend
    env_file: .env
    networks:
      - modir_net
    ports:
      - "8000:8000"
    volumes:
      - api_logs:/var/log/modir
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      vault:
        condition: service_started
      migrate:
        condition: service_completed_successfully

volumes:
  db_data:
  redis_data:
  minio_data:
  vault_data:
  api_logs:

networks:
  modir_net:
    driver: bridge
```

**Commit message:**
```
chore(compose): full docker stack with named volumes

Services: db, redis, minio, vault, migrate, api.
migrate exits before api starts (depends_on: service_completed_successfully).
All data on named volumes. Internal traffic on modir_net.
Using pgvector/pgvector:pg16 image (not postgres:16) — pgvector extension
needed from Phase 1 onward. Changing the image after data exists requires
wiping the volume; set it correctly from the start.
```

**Verification:**
- `docker compose up` brings everything up from a fresh clone
- `docker compose ps` shows api as `(healthy)`
- `curl localhost:8000/health` returns 200
- `docker compose down && docker compose up` — db data survives
- `docker compose down -v` — everything wiped

---

## Task 0.10 — GitHub Actions CI

**Branch:** `chore/MOD-0-ci`

CI runs on every push and PR. Lints. Type-checks. Verifies imports.
Phases 1+ will add real tests; for now, the goal is to prove the pipeline runs.

**File: `.github/workflows/ci.yml`**
```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Set up Python
        run: uv python install 3.11

      - name: Install dependencies
        working-directory: ./backend
        run: uv sync --frozen

      - name: Lint with ruff
        working-directory: ./backend
        run: uv run ruff check .

      - name: Format check with black
        working-directory: ./backend
        run: uv run black --check .

      - name: Import test
        working-directory: ./backend
        env:
          DATABASE_URL: postgresql+asyncpg://test:test@localhost/test
          REDIS_URL: redis://localhost:6379/0
          VAULT_ADDR: http://localhost:8200
          VAULT_TOKEN: test
        run: uv run python -c "from app.main import create_app; create_app()"

      - name: Forbidden patterns check
        run: |
          ! grep -rn "os.getenv" backend/app/ || (echo "Found os.getenv outside Settings"; exit 1)
          ! grep -rn "print(" backend/app/ || (echo "Found print() — use structlog"; exit 1)
          ! grep -rn "import requests" backend/app/ || (echo "Found requests — use httpx.AsyncClient"; exit 1)
```

**Commit message:**
```
ci: lint, format check, import test, forbidden patterns

Fails build on os.getenv, print(), or requests imports per constitution.
Real tests added in Phase 1+.
```

**Verification:**
- Push the branch and verify CI runs and passes
- Intentionally add `print("hi")` somewhere → CI fails
- Revert and verify CI passes again

---

## Task 0.11 — Pre-commit Hooks

**Branch:** `chore/MOD-0-precommit`

Catches issues locally before they reach CI.

**File: `.pre-commit-config.yaml`**
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: ['--maxkb=500']
      - id: check-merge-conflict
      - id: detect-private-key
```

**Commands:**
```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

**Commit message:**
```
chore(precommit): ruff, format, yaml, large-files, private-keys

Catches issues locally before CI runs.
```

---

## Phase 0 — Definition of Done

Run through this checklist before marking Phase 0 complete:

- [ ] `git clone && cp .env.example .env && docker compose up` works from a fresh machine
- [ ] `curl localhost:8000/health` returns `{"status":"ok"}`
- [ ] Killing the api container and bringing it back: data and logs survive
- [ ] `grep -rn "os.getenv\|print(\|import requests" backend/app/` returns nothing
- [ ] Vault is unreachable → api refuses to start with a clear error
- [ ] Migrations run automatically before api starts
- [ ] CI is green on `main`
- [ ] Pre-commit hooks installed and passing
- [ ] You can explain every line of `docker-compose.yml` out loud
- [ ] You can trace what happens from `docker compose up` to `/health` returning 200

## Phase 0 — Defend-it Preparation

Before moving to Phase 1, practice answering these out loud:

1. Why is `migrate` a separate service instead of running in the api container at startup?
2. Walk me through what happens when `docker compose up` is run on a fresh clone.
3. Where does the Gemini API key actually live? Show me the code that reads it.
4. Why `uv` and not `pip`?
5. What does your `lifespan` handler do today, and what will it do by the end of the project?
6. Why is the db service not exposed to the host?
7. What happens if I delete the `api_logs` volume? Should it matter?
8. Show me where structlog is configured. Why JSON output?
9. Why does CI grep for `requests` and fail if it finds it?
10. What's in your `.gitignore` and why is `.env` listed but `.env.example` is not?
11. Why is the Postgres image `pgvector/pgvector:pg16` and not `postgres:16`? What breaks if you switch images after the volume has data?

If you can't answer any of these without looking, the phase is not done.

## Ready for Phase 1?

You are ready when:
- All checkboxes above are checked
- All 10 defend-it questions can be answered fluently
- `docker compose up` brings up a clean, healthy stack with no errors in the logs

Phase 1 is The Wall — multi-tenancy and auth. It's the most important phase
in the project. Do not start it until Phase 0 is bulletproof.
