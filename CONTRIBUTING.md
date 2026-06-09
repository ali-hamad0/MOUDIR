# Contributing to Modir

## Before you start

Read `.specify/memory/constitution.md` first. Every contribution is evaluated
against the principles in that file — The Wall (tenant isolation), Vault secrets,
structured logging, and HIL enforcement are non-negotiable.

## Prerequisites

- Docker 24+ and Docker Compose v2
- Python 3.11 and [`uv`](https://github.com/astral-sh/uv)
- Node 20+ and npm

## Dev setup

```bash
git clone <repo> modir && cd modir
cp .env.example .env

# Backend
cd backend
uv sync --dev

# Frontend
cd ../frontend
npm install

# Start the full stack
cd ..
docker compose up
```

Run tests (offline, no running stack needed):

```bash
cd backend
uv run pytest -m "not integration and not load"
```

## Branch naming

```
feature/MOD-{n}-{short-slug}   # new feature
fix/MOD-{n}-{short-slug}       # bug fix
docs/MOD-{n}-{short-slug}      # documentation only
```

## Commit format

```
type(scope): short description

type: feat | fix | test | docs | refactor | chore
scope: api | agents | ml | frontend | infra | db | worker
```

Examples:
```
feat(api): add POST /orders/manual endpoint
fix(agents): retry on malformed tool argument
test(ml): add churn model golden eval
```

## Before submitting a PR

Run the full pre-PR checklist — CI enforces every item:

```bash
cd backend

# Linting and formatting
uv run ruff check .
uv run black --check .

# Forbidden patterns (CI hard-fails on these)
grep -rn "os.getenv" app/        # must return nothing
grep -rn "print("   app/        # must return nothing
grep -ri "import requests" app/  # must return nothing

# Tests
uv run pytest -m "not integration and not load"

# Frontend
cd ../frontend
npm run lint
npm run typecheck
npm run build
```

## Architecture rules (summary)

- **The Wall** — every DB query filters by `tenant_id`. No method queries without it.
- **Secrets in Vault** — no API key, password, or token in code, `.env`, or logs.
- **Structured logging** — `structlog` only, JSON output. No `print()`.
- **Async correctness** — never call a blocking SDK synchronously in an async route.
- **Layered dependencies** — `api/` → `services/` → `repositories/` → `db/`. A route
  never imports the ORM directly.
- **Provider-agnostic LLM** — all LLM calls go through the router in
  `app/agents/llm/router.py`. No direct provider SDK imports in application code.

See `.specify/memory/constitution.md` for the full rules and rationale.

## Opening a PR

1. Ensure all checklist items above pass.
2. Reference the task number in the PR title: `feat(agents): Task 9.3 demo seed script`.
3. Describe what changed, why, and how to test it.
4. Link to the relevant section of `DECISIONS.md` if your change involves an
   architectural trade-off.
