<div align="center">

<img src="frontend/public/logo.jpg" alt="Modir logo" width="180" height="180" />

# Modir · مودير

### AI Business Operations Assistant for Lebanese SMEs

Manage orders, inventory, finance, and customers over **WhatsApp** and a **web dashboard** — entirely in Lebanese Arabic.

[![CI](https://github.com/ali-hamad0/MOUDIR/actions/workflows/ci.yml/badge.svg)](https://github.com/ali-hamad0/MOUDIR/actions/workflows/ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![LangGraph](https://img.shields.io/badge/LangGraph-agents-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[Demo](#demo) · [Architecture](#architecture) · [Quick start](#quick-start) · [Docs](#documentation) · [For reviewers](#for-reviewers)

</div>

---

## Overview

Modir lets Lebanese small business owners manage orders, inventory, finance,
and customers through WhatsApp and a web dashboard, in Lebanese Arabic.
Five LangGraph AI agents handle different domains; customers place orders by
typing naturally in WhatsApp. When AI is unavailable, the owner can enter
orders manually — the business never stops.

### Highlights

- 🗣️ **Lebanese Arabic, end to end** — customers and owners both speak the dialect; the UI is RTL.
- 🤖 **Five specialist agents** — Order, Inventory, Finance, Customer, and Advisor, routed by a LangGraph supervisor.
- 🧱 **The Wall** — strict multi-tenant isolation enforced in code on every row and every query.
- 🧠 **ML predicts, LLMs explain** — trained models forecast demand and flag churn; agents put it in words.
- 🧾 **Paper to data** — OCR turns photographed supplier bills into structured inventory and finance entries.
- 🛟 **Always-on** — if AI providers fail, manual order entry keeps the shop running.

## Demo

Seed a Lebanese bakery with realistic data, then follow the demo script:

```bash
docker compose up -d
docker compose exec api python -m scripts.seed_demo
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Login: `demo@modir.test` / `DemoPassword1`

## Architecture

```mermaid
flowchart TD
    WA[WhatsApp\nCustomer / Owner]
    WH[webhook POST /webhooks/whatsapp]
    DISP[MessageDispatcher]

    subgraph Customer path
        OA[OrderAgent\nLangGraph]
    end

    subgraph Owner path
        SUP[OwnerSupervisor\nLangGraph]
        IA[InventoryAgent]
        FA[FinanceAgent]
        CA[CustomerAgent]
        AA[AdvisorAgent]
    end

    MAN[POST /orders/manual\nDashboard fallback]
    DB[(Postgres 16\n+ pgvector)]
    REDIS[(Redis 7\nRate limit)]
    MINIO[(MinIO\nBill images)]
    VAULT[(HashiCorp Vault\nSecrets)]
    LOKI[(Grafana Loki\nLogs)]
    GRAFANA[Grafana\nDashboards]

    WA -->|HTTPS| WH
    WH -->|rate limit check| REDIS
    WH --> DISP
    DISP -->|customer msg| OA
    DISP -->|owner msg| SUP
    SUP --> IA & FA & CA & AA
    OA & IA & FA & CA & AA --> DB
    MAN --> DB
    DISP -->|AI down| MAN

    DB --- VAULT
    MINIO --> VAULT
    LOKI --> GRAFANA
    WH --> LOKI
```

## Tech stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11 · FastAPI · SQLAlchemy 2.x (async) · Alembic · structlog |
| **AI / Agents** | LangGraph · Gemini Flash (Tier 1) · Gemini Pro (Tier 2) · LangSmith tracing |
| **Data** | Postgres 16 + pgvector · Redis 7 · MinIO |
| **Secrets** | HashiCorp Vault |
| **Frontend** | React 18 · Vite · Tailwind (RTL) |
| **Ops** | Docker Compose · Grafana + Loki · GitHub Actions CI |

## Prerequisites

- Docker 24+ and Docker Compose v2
- Git

## Quick start

```bash
git clone <repo-url> modir
cd modir
cp .env.example .env          # review and set VAULT secrets if needed
docker compose up             # starts db, redis, minio, vault, vault-init, migrate, api, worker

# FIRST RUN ONLY: vault-init initializes Vault (unseal key + root token land in
# secrets/vault-init.txt, gitignored) — then seed the secret paths once. Vault
# data persists from now on; change values manually in the UI
# (http://localhost:8200, token "root") or via `vault kv patch`.
docker compose --profile seed run --rm vault-seed
```

The API is now available at `http://localhost:8000`.
The dashboard (Vite dev server) runs separately:

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

## Run tests

```bash
cd backend
uv run pytest -m "not integration and not load"   # fast, offline
uv run pytest                                      # all tests (requires Postgres on localhost:5432)
```

## Observability stack (Grafana + Loki)

```bash
# Add to .env:
LOKI_URL=http://loki:3100

docker compose --profile observability up
```

Grafana is at `http://localhost:3000` (anonymous read access enabled).
The Modir dashboard loads automatically with 6 panels: request rate, error rate,
LLM fallback activations, rate-limit hits, per-tenant cost, and agent latency.

## Backup & restore

```bash
# Create a backup
docker compose --profile backup run --rm backup

# Restore from the most recent backup
docker compose --profile backup run --rm backup /app/scripts/restore.sh
```

Backups land in `backups/postgres/` (gzipped pg_dump) and `backups/minio/`
(MinIO object mirror). See [RUNBOOK.md](RUNBOOK.md) for detailed failure playbooks.

## Load test

```bash
cd backend
uv run pytest tests/load/ -m load -v
```

Requires the full Docker Compose stack running. Tests 100 concurrent requests
across 10 tenants and asserts zero cross-tenant data leakage.

## Documentation

- [RUNBOOK.md](RUNBOOK.md) — failure scenarios and recovery commands
- [DECISIONS.md](DECISIONS.md) — every architectural decision, Phase 0 → Phase 8

## For Reviewers

- Seed demo data: `docker compose exec api python -m scripts.seed_demo`

**Key architectural claims to verify:**

| Claim | Where to look |
|-------|---------------|
| Tenant isolation in every query | `backend/app/repositories/base.py` — `_require_tenant_scope` |
| Secrets only in Vault, never in code | `backend/app/infra/vault.py` — `resolve_secrets` |
| HIL gate on every Level-2 action | `backend/app/infra/action_gate.py` — `ActionGate.authorize` |
| ML predicts, LLM explains | `backend/app/ml/` (models) vs `backend/app/agents/` (language) |
| No `os.getenv`, `print(`, `import requests` in app code | `grep -rn "os.getenv\|print(\|import requests" backend/app/` |
| Red-team block rate >= 92% | `backend/app/agents/eval/agent_thresholds.yaml` + CI step |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch naming, commit format, and the
code standards in [`.specify/memory/constitution.md`](.specify/memory/constitution.md).
Security policy: [SECURITY.md](SECURITY.md).

## License

Released under the [MIT License](LICENSE) © 2026 Ali Hamad.

<div align="center">
<sub>Built for Lebanese small businesses — صُنع لأصحاب المحلات في لبنان 🇱🇧</sub>
</div>
