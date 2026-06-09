# ---- Builder stage: install dependencies with the toolchain available ----
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120

WORKDIR /app

# build-essential is needed only to compile any packages without prebuilt
# wheels. It lives in this stage and is discarded — never shipped at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Resolve and install dependencies into a project-local .venv, no dev deps.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---- Runtime stage: slim image with only what's needed to run ----
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy the prebuilt virtual environment from the builder. No toolchain, no uv.
COPY --from=builder /app/.venv /app/.venv

COPY app ./app
# Lebanese-Arabic prompt copy lives in prompts/ (constitution: never inline in
# agent code). The agents import `from prompts import ...`, so the package must be
# in the image — the api gets it via the dev bind-mount, but the worker runs the
# baked image, so it MUST be copied here.
COPY prompts ./prompts
COPY alembic ./alembic
COPY alembic.ini ./

RUN mkdir -p /var/log/modir

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# .venv is on PATH, so uvicorn runs directly — no `uv run` needed at runtime.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
