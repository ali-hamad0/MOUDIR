# Modir — Architecture Decision Record

One entry per significant decision. Format: **Decision → Alternatives → Reason → Date.**

---

## Phase 0 — Foundation

### AD-0.1 — Package manager: uv, not pip or poetry

**Decision:** Use `uv` for all Python dependency management.
**Alternatives:** `pip` + `requirements.txt`; `poetry`.
**Reason:** `uv` is 10–100x faster than pip, has a lock file like poetry, and
requires no plugins or shell shims. Single command install. Lebanese SME timeline
is short — slow CI is not acceptable.
**Date:** 2024-Q4

### AD-0.2 — Secrets: HashiCorp Vault dev mode

**Decision:** All secrets (LLM keys, JWT signing key, DB credentials, MinIO keys)
are stored in and fetched from Vault at startup. Never hardcoded or in `.env` files
committed to the repo.
**Alternatives:** `.env` files (gitignored); AWS Secrets Manager; plain environment
variables passed by the operator.
**Reason:** The business handles customer data. Hardcoded secrets in environment
variables have a long history of leaking into logs, crash reports, and Docker
inspect output. Vault's KV store enforces rotation and audit trails. Dev mode is
used locally; production would use Vault with integrated storage.
**Date:** 2024-Q4

### AD-0.3 — Structured logging: structlog + JSON renderer

**Decision:** All logging goes through `structlog` with a JSON renderer in
production and a pretty-print console renderer in development.
**Alternatives:** Python stdlib `logging` only; `loguru`.
**Reason:** JSON logs are grep-able, Loki-ingestible, and parseable by every log
aggregator without a custom parser. `structlog` binds context (tenant_id,
request_id) once per request and threads it through every log line automatically.
**Date:** 2024-Q4

### AD-0.4 — ORM: SQLAlchemy 2.x async, not Django ORM or tortoise-orm

**Decision:** SQLAlchemy 2.x with `AsyncSession` + Alembic for migrations.
**Alternatives:** Django ORM (requires Django); Tortoise ORM; raw asyncpg.
**Reason:** SQLAlchemy is the de-facto standard for Python ORMs. Version 2.x's
async API is first-class. Alembic gives reproducible, reviewable migrations.
Raw asyncpg would require hand-rolling query builders — not worth the maintenance
cost for a multi-tenant schema with 15+ tables.
**Date:** 2024-Q4

---

## Phase 1 — The Wall (Multi-Tenancy)

### AD-1.1 — Tenant isolation: tenant_id on every row, not separate schemas

**Decision:** Every table has a `tenant_id UUID NOT NULL` column. Repositories
are forced to filter by it via an abstract base class. No separate Postgres
schemas per tenant.
**Alternatives:** One Postgres schema per tenant (schema-per-tenant); separate
databases per tenant; row-level security via Postgres RLS.
**Reason:** Schema-per-tenant requires dynamic schema switching and complicates
migrations — Alembic would need to run against N schemas. Postgres RLS is
powerful but invisible to the application layer; a bug in the policy silently
leaks data with no Python traceback. Explicit `tenant_id` in every query is
visible, testable, and grep-able. The abstract `BaseRepository` makes it
impossible to write a query that forgets the filter.
**Date:** 2024-Q4

### AD-1.2 — Two identity tables: `users` (dashboard) and `tenant_owners` (WhatsApp)

**Decision:** Dashboard login and WhatsApp owner identity are separate tables and
separate authentication paths. A dashboard user (email + JWT) and a WhatsApp
owner (phone number) are different concepts even when they represent the same
human.
**Alternatives:** Single `users` table with a `phone_number` field; OAuth-only.
**Reason:** WhatsApp messages arrive without a JWT. The webhook must resolve
identity from phone numbers alone. Conflating dashboard auth with WhatsApp
identity would require the owner to "log in" before their first message — which
breaks the WhatsApp UX entirely.
**Date:** 2024-Q4

### AD-1.3 — JWT algorithm: HS256, secret from Vault

**Decision:** JWT tokens use HS256 with a signing secret stored in Vault at
`secret/modir/auth.jwt_secret`. Short-lived (60 min default), no refresh tokens
in Phase 1.
**Alternatives:** RS256 (asymmetric); Supabase auth; Firebase auth.
**Reason:** HS256 is sufficient for a single-backend deployment where the same
service signs and verifies tokens. Asymmetric keys are valuable in microservice
meshes — overkill here. External auth providers add a hard runtime dependency and
lock-in risk.
**Date:** 2024-Q4

---

## Phase 2 — Orders & Customer Flow

### AD-2.1 — Order state machine: explicit `status` enum, not event sourcing

**Decision:** Orders have a `status` column with values `{pending, confirmed,
preparing, ready, delivered, cancelled}`. Transitions are validated in the service
layer.
**Alternatives:** Full event sourcing (event log + projections); CQRS.
**Reason:** Event sourcing adds significant complexity (event schema evolution,
projection rebuilds, eventual consistency) for a business with 50–200 orders/day.
The status column is auditable enough; `audit_log` captures who changed what.
Phase 9+ can migrate to event sourcing if analytics demands it.
**Date:** 2024-Q4

### AD-2.2 — `business_policies` key-value table for per-tenant config

**Decision:** Tenant configuration (rate limits, LLM budget, webhook URLs, plan
limits) is stored as `(tenant_id, key, value: text)` rows in `business_policies`,
not as typed columns on the `tenants` table.
**Alternatives:** JSONB column on `tenants`; typed columns per setting; a Redis
hash per tenant.
**Reason:** New configuration keys are added every phase without an Alembic
migration. JSONB is hard to query by key and doesn't support per-key audit trails.
Redis is ephemeral. The KV table is simple, indexed on `(tenant_id, key)`, and
visible in the DB.
**Date:** 2024-Q4

---

## Phase 3 — Onboarding & Dashboard

### AD-3.1 — Frontend: React 18 + Vite, not Next.js

**Decision:** Single-page React app with Vite, TypeScript, and React Router.
**Alternatives:** Next.js (SSR); Remix; plain HTML + Alpine.js.
**Reason:** The dashboard is a private authenticated app — SEO is irrelevant, so
SSR adds cost without benefit. Vite's HMR is faster than Next.js for a solo
frontend developer. No backend coupling needed: the React app talks to the FastAPI
JSON API directly.
**Date:** 2024-Q4

### AD-3.2 — i18n: static `i18n.ts` object, not i18next

**Decision:** All UI strings live in `frontend/src/i18n.ts` as a typed TypeScript
object. No i18n library, no translation files, no locale switching.
**Alternatives:** `react-i18next`; `lingui`; hardcoded strings.
**Reason:** Modir has exactly one UI language (Lebanese Arabic). A full i18n
library adds ~40KB to the bundle and runtime complexity for a feature that will
never be used. Typed object keys give compile-time safety that a library's string
keys do not.
**Date:** 2024-Q4

---

## Phase 4 — Inventory & Supplier Bills

### AD-4.1 — HIL (Human-in-the-Loop) gate: `ActionGate` + approval tokens

**Decision:** Level-2 actions (writes with business impact: create order, mark
product unavailable, etc.) require an approval token before execution. The agent
proposes; the owner approves via WhatsApp reply. The gate is enforced in code via
`ActionGate.require_approval()`, not in prompts.
**Alternatives:** Trust the agent unconditionally; require approval for all
actions; use a separate approval microservice.
**Reason:** LLMs hallucinate. An agent that can autonomously delete inventory or
confirm large orders without owner approval is a liability. The gate is
architecturally enforced — there is no code path that bypasses it for Level-2
actions. Prompting the LLM to "always ask" is insufficient: it can be jailbroken.
**Date:** 2025-Q1

---

## Phase 5 — OCR (Supplier Bill Extraction)

### AD-5.1 — OCR runtime: Gemini Vision API, not Tesseract or AWS Textract

**Decision:** Supplier bill images are sent to Gemini's vision endpoint for
structured extraction. No local OCR binary.
**Alternatives:** Tesseract (local, free); AWS Textract; Azure Form Recognizer.
**Reason:** Lebanese handwritten bills and mixed Arabic/English text defeat
Tesseract reliably. Gemini Vision handles both scripts and returns structured JSON
without custom model training. Textract/Azure would require separate cloud
accounts, adding complexity and cost for a small business. We already pay for
Gemini for the chat agents.
**Date:** 2025-Q1

### AD-5.2 — OCR worker: separate process, same Docker image

**Decision:** OCR runs in a dedicated `worker` container using the same Docker
image as the API, but with `command: python -m app.worker`. Bills are polled from
the DB (status = `uploaded`), not from a message queue.
**Alternatives:** Celery + Redis queue; a separate Python service; inline in the
webhook handler.
**Reason:** Celery adds a broker dependency (RabbitMQ or Redis as a queue, not
just a cache) and operational complexity. Inline OCR blocks the webhook response
for 3–10 seconds — unacceptable. DB-polling at 5s intervals is simple, reliable,
and self-healing: if the worker crashes, it picks up where it left off on restart.
**Date:** 2025-Q1

---

## Phase 6 — ML Layer (Routing + Embeddings)

### AD-6.1 — ML training: code-first in `app/ml/`, Colab is optional compute

**Decision:** Training scripts live in `backend/app/ml/` as Python modules.
One command retrains: `uv run python -m app.ml.train_all`. Google Colab is an
optional compute target (export data, run there, import weights back) — it is not
the primary workflow.
**Alternatives:** Notebook-first (Jupyter as the canonical training artifact);
MLflow; SageMaker.
**Reason:** Notebooks are hard to review in git diffs, can't be imported as
modules, and drift from production code. The `app/ml/` module structure means
training and inference share the same data-loading and preprocessing code with no
translation layer.
**Date:** 2025-Q1

### AD-6.2 — Routing model: intent classifier → LLM router, not LLM-only routing

**Decision:** A small intent classifier (trained on Lebanese Arabic queries)
pre-routes messages to the correct agent before the LLM supervisor decides. The
LLM supervisor handles ambiguous cases the classifier scores below threshold.
**Alternatives:** LLM-only routing (every message goes to the supervisor prompt);
keyword matching; separate embedding model per agent.
**Reason:** LLM routing costs money on every message, even simple ones. A fast
local classifier handles 80%+ of messages at near-zero marginal cost. The
classifier's training data is the golden eval set from Phase 8 — so the eval data
and training data share the same distribution.
**Date:** 2025-Q1

---

## Phase 7 — Agent Supervisor

### AD-7.1 — Supervisor: LangGraph StateGraph, not a hand-rolled dispatch loop

**Decision:** The OwnerSupervisor is a LangGraph `StateGraph` with nodes for each
of the five specialist agents and conditional edges based on intent classification.
**Alternatives:** Hand-rolled `if/elif` dispatch; a single monolithic prompt;
LangChain Agents (ReAct loop).
**Reason:** LangGraph's graph structure makes agent routing explicit and
inspectable. State checkpoints (persisted in Postgres via `AsyncPostgresSaver`)
allow multi-turn conversations to resume after crashes. A monolithic prompt would
balloon in size with every new domain. ReAct loops are hard to constrain to
specific tool sets.
**Date:** 2025-Q1

### AD-7.2 — LLM fallback: three-tier router with circuit breaker

**Decision:** `FallbackLLMRouter` tries Gemini Flash → Gemini Pro → Grok →
Anthropic Claude in order. Each provider has a circuit breaker: if it fails, it
is skipped for 60 seconds. If all fail, the supervisor returns the Lebanese Arabic
"unavailable" message.
**Alternatives:** Single provider with retry; always fall back to a cached
response; queue the message for later.
**Reason:** LLM providers have independent outages. A three-provider fallback
gives 99.9%+ practical availability. Queueing requires the customer to wait
indefinitely — unacceptable for a chat UX. Cached responses hallucinate current
inventory.
**Date:** 2025-Q1

### AD-7.3 — Conversation checkpoints: Postgres, not Redis or in-memory

**Decision:** LangGraph conversation state is checkpointed in Postgres via
`AsyncPostgresSaver` (the official LangGraph Postgres checkpointer).
**Alternatives:** Redis (fast but ephemeral); in-memory (lost on restart); a
custom file-based store.
**Reason:** Postgres is already in the stack and is durable. Redis would lose
conversation history on a container restart — a customer mid-order would lose
context. The `AsyncPostgresSaver` is maintained by the LangGraph team and handles
serialization correctly.
**Date:** 2025-Q1

---

## Phase 8 — Hardening & Production Readiness

### AD-8.1 — Rate limiter: Redis INCR/EXPIRE per-tenant, not nginx rate limiting

**Decision:** Rate limiting is implemented in Python as a FastAPI dependency
(`RateLimiter`) using Redis `INCR` + `EXPIRE`. Key: `rate_limit:{tenant_id}:{window_minute}`.
Applied to the webhook router. Default: 30 req/min (free), 120 req/min (paid).
Returns HTTP 429 with `Retry-After` header and Lebanese Arabic body.
**Alternatives:** nginx `limit_req_zone` (per-IP); AWS API Gateway throttling;
a standalone rate-limit service (Redis-rate-limit).
**Reason:** IP-based rate limiting is wrong for this product: multiple tenants
share a NAT IP (a common ISP configuration in Lebanon). The rate limit must be
per-tenant. A Python dependency keeps the logic in the application layer where
it can be tested, overridden per-tenant via `business_policies`, and bypassed for
internal services — without an nginx config deploy.
**Date:** 2025-Q2

### AD-8.2 — Golden evals: per-agent JSONL files, LLM-as-judge via mock

**Decision:** Each agent has a golden eval set in `app/agents/eval/golden/{agent}.jsonl`
(20+ entries). A separate `redteam.jsonl` covers injection and jailbreak attacks
(25+ entries). CI runs the evaluator and exits 1 on regression.
**Alternatives:** Pytest-only tests (no LLM call); production traffic sampling;
a commercial eval platform (Braintrust, LangSmith).
**Reason:** Pytest unit tests can't catch LLM behavioral drift. Production
sampling requires production traffic (which we don't have yet). A commercial
platform adds cost and an external dependency. JSONL golden sets are versionable,
reviewable in PRs, and run offline with a mock LLM — the same mock used in all
other tests.
**Date:** 2025-Q2

### AD-8.3 — Load test: asyncio + httpx, not Locust or k6

**Decision:** Load tests use `httpx.AsyncClient` with `asyncio.gather` for 100
concurrent requests. Marked `@pytest.mark.load`, excluded from the default suite.
**Alternatives:** Locust; k6; Apache JMeter.
**Reason:** External load testing tools require separate processes, separate
config files, and non-Python DSLs. `httpx` + asyncio runs inside pytest, shares
fixtures and mocks, and produces readable Python assertions on isolation. 100
concurrent requests against a local stack is sufficient to prove The Wall holds
under load — we don't need to simulate 10,000 req/s at this phase.
**Date:** 2025-Q2

### AD-8.4 — Chaos tests: code-boundary simulation, not Docker stop

**Decision:** Chaos tests patch Python internals (mock `hvac.Client`,
`AsyncSession.execute`, provider adapters) to simulate failures. No Docker CLI
calls, no `subprocess.run("docker stop db")`.
**Alternatives:** Docker SDK to kill containers; `toxiproxy` for network-level
chaos; `chaos-mesh` in Kubernetes.
**Reason:** Docker stop/start in CI is flaky, environment-dependent, and slow
(containers take seconds to die and recover). `toxiproxy` adds an external process
dependency. Python-level mocks run in milliseconds, are deterministic, and work
identically in CI and on a developer's laptop.
**Date:** 2025-Q2

### AD-8.5 — Graceful degradation: automatic circuit breaker in dispatcher, not a feature flag

**Decision:** When all LLM providers are exhausted, `MessageDispatcher` catches
`LLMUnavailable` and returns the Arabic "unavailable" reply automatically. `GET
/health/ai` exposes the circuit state. No feature flag, no manual operator action
required.
**Alternatives:** Feature flag to enable "maintenance mode"; dead letter queue
(retry later); silent failure (no reply).
**Reason:** A feature flag requires an operator to notice the failure, log in,
and toggle a flag — impossible at 2am. Silent failure leaves the customer thinking
their message was received. A dead letter queue requires the customer to wait an
undefined time. Automatic graceful degradation with a clear Arabic message is the
only UX that respects the customer.
**Date:** 2025-Q2

### AD-8.6 — Log aggregator: Grafana Loki, not Elasticsearch or Datadog

**Decision:** Loki + Grafana run under `docker compose --profile observability`.
A custom `LokiHandler` (stdlib `logging.Handler` subclass, ~80 lines) ships logs
via a background daemon thread. Unreachable Loki is silently ignored.
**Alternatives:** Elasticsearch + Kibana (ELK); Datadog; Splunk; Cloudwatch.
**Reason:** ELK requires 3–4 containers and 4–8GB RAM — too heavy for a dev
machine. Datadog/Splunk/Cloudwatch are paid SaaS with external dependencies.
Loki is lightweight (label-based index only, not full-text), integrates natively
with Grafana (which we add for dashboards anyway), and is self-hosted. The custom
handler avoids the `python-logging-loki` package (which spawns a thread per
handler instantiation and has poor error handling).
**Date:** 2025-Q2

### AD-8.7 — Cost alerts: background task in existing worker, webhook delivery

**Decision:** `CostAlertTask` runs hourly in `app/worker.py` (the existing worker
loop). Alerts are delivered via HTTP POST to a configurable `alert_webhook_url`
stored in `business_policies`. Deduplication uses a `cost_alert_last_sent`
timestamp in the same KV table.
**Alternatives:** Cron job (separate container); Celery beat; email delivery;
real-time alerts on every LLM call.
**Reason:** Cron in a container requires `crond` or a separate cron image.
Celery beat requires the Celery broker. The existing worker already has a task
loop — adding a 60-minute check costs ~10 lines. Webhook delivery is generic:
Slack, Make.com, email relays, and custom scripts all accept webhooks. Real-time
alerts per LLM call would fire hundreds of times per day.
**Date:** 2025-Q2

### AD-8.8 — Backup: pg_dump + Python minio SDK, not pgbackrest or WAL shipping

**Decision:** `scripts/backup.sh` uses `pg_dump | gzip`. Object storage is
mirrored using the `minio` Python SDK (already a project dependency) instead of
the `mc` binary or `pgbackrest`.
**Alternatives:** `pgbackrest` (WAL archiving + PITR); `barman`; `pg_basebackup`;
`mc mirror` binary.
**Reason:** WAL shipping and PITR are valuable for high-traffic production
databases requiring sub-minute RPO. At Modir's scale (50–200 orders/day), a
daily `pg_dump` gives an acceptable RPO and is orders of magnitude simpler to
operate and verify. The `mc` binary requires an external download at Docker build
time, which fails on the WSL/Hyper-V firewall in this environment. The `minio`
Python SDK is already installed in the `.venv` and works identically.
**Date:** 2025-Q2
