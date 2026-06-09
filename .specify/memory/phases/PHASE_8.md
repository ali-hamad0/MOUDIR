# Phase 8 — Hardening & Production Readiness

> Modir can survive a real day of real traffic. Tests fail CI on regression.
> Logs are queryable. Costs are tracked. Failure modes are graceful. The system
> degrades intentionally, never catastrophically.

Read `.specify/memory/constitution.md` FIRST (Principles I, II, III, V all
apply heavily here). Then read `.specify/memory/ROADMAP.md` (Phase 8 section).
Then this file. Work task by task; pause for approval after each task before
committing.

---

## Goal

Make the Phase 7 system survive real conditions:

1. **Rate limiting** — abusive tenants cannot flood the system
2. **Agent golden evals** — every agent's behaviour is pinned and CI-guarded
3. **Red-team eval** — injection / jailbreak attacks are measurably blocked
4. **Load testing** — 100 concurrent customers across 10 tenants, zero data leak
5. **Chaos tests** — Vault kill, LLM kill, DB kill → graceful recovery, not crashes
6. **Graceful degradation** — manual order entry works when AI is entirely down
7. **Log aggregator** — structured logs flow to Grafana Loki, queryable in seconds
8. **Cost dashboards + alerts** — per-tenant LLM spend is visible and alertable
9. **Backup + restore** — Postgres and MinIO backup tested at least once; runbook written
10. **README + architecture diagram** — one command, one clone, Modir runs; every
    decision is documented

---

## Resist scope creep

- **No new agents.** Phase 7 shipped all five specialists. Phase 8 tests, hardens,
  and observes them — it does not add new ones.
- **No new ML models.** Phase 6 models are the ML layer. This phase does not
  retrain, replace, or add models.
- **No WhatsApp API integration.** That is Phase 10 (Meta approval required).
  The manual order entry in Phase 8 is a dashboard-only fallback.
- **No UI polish.** The frontend gets a cost dashboard page and a manual order form.
  No redesign, no animation, no new branding. [[phase3-frontend-polish-pending]].
- **Log aggregator is dev/staging only.** Loki + Grafana ship in `docker-compose.yml`
  behind a dev profile. Production deployment is out of scope for Phase 8.
- **Load tests are manual.** They run locally or in CI on demand — not in the main
  `pytest` suite (they require a running stack and take minutes).

---

## Prerequisites (read before Task 8.1)

- **Phase 7 is complete and merged to main.** `app.state.supervisor` is wired;
  all five agents work; Postgres checkpoints resume; the fallback LLM router is
  live; `uv run pytest -m "not integration"` is green.
- **Cost tracking (Task 7.9) is live.** `agent_runs` table exists; `CostTrackingCallback`
  writes a row per LLM call. Phase 8 builds cost *dashboards* on top of this — it
  does not re-implement the tracking layer.
- **Guardrails are consolidated (Task 7.12).** All five agents pass output through
  `check_output`. Phase 8 extends the guardrails evaluation to a CI-blocking
  red-team golden set — it does not rebuild the guardrails logic.
- **Redis is in the stack.** Phase 8 uses Redis for rate limiting. It is already
  in `docker-compose.yml` and `Settings`.

---

## Core constraint reminders

### Constitution I — The Wall

- Rate limiter keys are `f"rate_limit:{tenant_id}"` — never by IP alone, as
  multiple tenants can share a NAT. Separate counters per tenant is The Wall
  at the infrastructure layer.
- The load test MUST assert cross-tenant isolation, not just throughput. The
  test is meaningless without the isolation assertion.
- Manual order endpoint (`POST /orders/manual`) goes through `get_current_user`
  → `tenant_id` in JWT → repo filtered by `tenant_id`. No shortcut.

### Constitution II — Secrets live in Vault

- The chaos test for Vault kill asserts that `api` refuses to start with a
  clear error message, not that it silently degrades. The `Settings` class
  already raises on missing secrets — the test confirms this path.
- Backup scripts must NOT write Vault secrets to the backup. Only the data and
  object store are backed up; secrets are re-seeded from `scripts/vault-seed.sh`.

### Constitution III — Observability

- Every rate limit hit is a structured log line: `{tenant_id, endpoint, limit,
  reset_at}`. PII-free — no phone number in the log.
- Every chaos event (LLM fallback exhausted, DB disconnect, Vault unreachable)
  produces a structured log entry at `WARNING` or `ERROR` level. These are the
  lines the runbook tells the on-call engineer to grep for.
- The log aggregator task (8.6) is the delivery of constitution III's "logs are
  queryable" requirement. Logs in stdout are not enough for production.

### Constitution V — HIL is architecturally enforced

- Manual order entry (Task 8.5) bypasses the agent, but does NOT bypass the HIL
  gate for level-2 actions. A manually created order is Level 1 (the human is
  the operator). No approval token needed for direct dashboard entry — the
  human IS the approval.

---

## Architecture decisions (recorded — belong in DECISIONS.md at Task 8.9)

### AD-8.1 — Rate limiter: Redis token bucket, per-tenant key

Redis `INCR` + `EXPIRE` pattern (token-bucket approximation). Key:
`rate_limit:{tenant_id}:{window_start_minute}`. Limit configurable in
`business_policies` table (`key = "rate_limit_rpm"`, default from `Settings`).
Plan-tier defaults: free → 30 req/min; paid → 120 req/min. Returns HTTP 429
with `Retry-After` header and Lebanese Arabic body. Implemented in
`app/infra/rate_limiter.py` as a FastAPI dependency, applied to the webhook
router. Not applied to dashboard API (owner uses JWT, rate limiting there is
different — out of scope for Phase 8).

### AD-8.2 — Golden evals: per-agent JSONL files, LLM-as-judge via mock

Each agent's golden set lives in `app/agents/eval/golden/{agent_name}.jsonl`.
Each entry: `{query_ar, expected_tools: [str], expected_language: "ar",
intent_tag: str}`. The eval runner uses the existing mock LLM infra from Phase 7
(`ROUTING_EVAL_REAL_LLM=0` by default). Passing criteria: correct tool sequence
called ≥ 85% of the time per agent; all responses contain Arabic characters
(language gate); no hallucinated product names in output. Red-team set: separate
file `app/agents/eval/golden/redteam.jsonl` with 25+ injection / jailbreak
attempts; block rate must be ≥ 0.92.

### AD-8.3 — Load test: httpx + asyncio, offline DB fixture

Load test uses `httpx.AsyncClient` in asyncio gather — no Locust dependency
(constitution: prefer simplest tool). Seeds 10 tenants × 10 products × 50
customers via the existing conftest fixture pattern. 100 concurrent POST
requests to `/webhooks/whatsapp` (mocked LLM, real DB). Isolation assertion:
each response's `order.tenant_id` matches the `tenant_id` of the request sender.
No cross-tenant order ID appears in any response. Runs via
`pytest tests/load/ -m load` (excluded from normal `pytest tests/`).

### AD-8.4 — Chaos strategy: test-level simulation, not Docker stop

Chaos tests simulate failures at the code boundary, not by actually killing
containers (Docker CLI in CI is fragile). Vault: mock `hvac.Client.secrets.kv`
to raise `VaultError` → assert lifespan raises with `SystemExit`. LLM: mock all
three provider adapters to raise `APIError` → assert supervisor returns the
graceful Lebanese Arabic "unavailable" reply and logs `LLMUnavailable`. DB:
mock `asyncpg.Connection.execute` to raise `asyncpg.PostgresConnectionError` →
assert endpoints return 503 (not 500). Each chaos case has its own clearly named
test in `tests/test_chaos.py`.

### AD-8.5 — Graceful degradation: circuit breaker in dispatcher

`MessageDispatcher.handle_owner` wraps the supervisor call in a try/except for
`LLMUnavailable`. On `LLMUnavailable`: reply to WhatsApp with the Arabic
"unavailable" message (from `prompts/system_unavailable_ar.md`), log the event
at WARNING. Dashboard gets a `/health/ai` endpoint returning `{available: bool}`.
Frontend shows an "AI temporarily unavailable — manual entry mode" banner when
`available == false`. The `POST /orders/manual` endpoint is always available
(it never calls the supervisor). This is not a feature flag — it is automatic
and transparent.

### AD-8.6 — Log aggregator: Grafana Loki, structlog Loki handler

Loki is the aggregator choice: free, self-hosted, integrates with Grafana
(which we add anyway for dashboards), and has a structlog-compatible HTTP push
endpoint. The structlog pipeline gains a `LokiHandler` processor (custom, ~50
lines) after the existing JSON renderer — it POSTs log lines to Loki in a
background thread (non-blocking). Grafana pre-built dashboard JSON stored in
`infra/grafana/dashboards/modir.json` — includes: request rate by tenant,
error rate, LLM fallback events, rate limit hits, cost per day per tenant.
Both Loki and Grafana run under the `docker compose --profile observability`
profile so they do not start on plain `docker compose up`.

### AD-8.7 — Cost alerts: background task in worker, webhook delivery

A `CostAlertTask` runs every hour inside `app/worker.py` (the existing worker
already has a task loop). For each tenant: query `agent_runs` for today's total
`cost_usd`. Compare against `business_policies.rate_limit_rpm`... actually,
against a new policy key `daily_llm_budget_usd` (default: `0` = no limit). If
budget is exceeded and last alert was > 1 hour ago: POST to a configurable
`alert_webhook_url` from `business_policies`. The frontend cost dashboard reads
from the existing `GET /admin/costs` endpoint (Task 7.9); a new `GET
/admin/costs/summary` returns the current-day spend + budget + percentage for
the alert banner.

### AD-8.8 — Backup: pg_dump + MinIO mirror, no PgBackrest

PgBackrest adds complexity without benefit at this scale. `pg_dump` (logical
backup) is sufficient for a single-instance dev/staging Postgres. `scripts/
backup.sh`: `pg_dump | gzip > backups/postgres/YYYYMMDD_HHMM.sql.gz` and
`mc mirror minio/uploads backups/minio/`. `scripts/restore.sh`: `gunzip | psql`
from the latest timestamped file + `mc mirror` back to MinIO. Both scripts
are idempotent. A `backup` service in `docker-compose.yml` runs
`scripts/backup.sh` on a 24h sleep loop (cron is overkill in a container).
RUNBOOK.md documents the exact commands for each failure scenario.

---

## New data model (Task 8.1 + 8.7)

One new policy key (not a migration — stored in the existing `business_policies`
table as a key/value row):

```
business_policies
  key = "daily_llm_budget_usd"   value = "10.00"   (default: "0" = no limit)
  key = "rate_limit_rpm"         value = "30"       (already used in Phase 4+)
  key = "alert_webhook_url"      value = "https://..."  (Slack/email relay)
```

No new Alembic migration is needed for Phase 8. All new storage uses:
- existing `agent_runs` (cost data, Task 7.9)
- existing `business_policies` (config, Phase 1)
- existing `audit_log` (guardrail trips, Phase 1)
- Redis (rate limit counters, ephemeral — no schema)
- `backups/` volume (outside Alembic scope)

---

## Phase 8 — Tasks Overview

All tasks live on **one branch**: `feature/MOD-8-hardening`.
One commit per task. One PR at the end of the phase.

| # | Task | What it delivers |
|---|------|-----------------|
| 8.1 | Per-tenant rate limiting | Redis token bucket on `/webhooks/whatsapp`; 429 with Arabic message; independent counters per tenant |
| 8.2 | Agent golden evals + red-team CI gate | 20+ Arabic queries per agent; block-rate gate for injections; CI blocks on regression |
| 8.3 | Load test: 100 concurrent / 10 tenants | asyncio + httpx concurrent test; isolation assertion on every response |
| 8.4 | Chaos tests: Vault / LLM / DB kill | Simulated failures at code boundary; graceful recovery asserted, not hoped for |
| 8.5 | Graceful degradation + manual order entry | `POST /orders/manual`; AI circuit breaker in dispatcher; "unavailable" banner in frontend |
| 8.6 | Log aggregator: Grafana Loki | Loki + Grafana in docker-compose `observability` profile; pre-built dashboard JSON |
| 8.7 | Cost dashboards + threshold alerts | Budget policy key; hourly alert task; frontend cost page |
| 8.8 | Backup + restore + RUNBOOK.md | `scripts/backup.sh`, `scripts/restore.sh`; restore proven under 15 min; runbook written |
| 8.9 | README + architecture diagram + DECISIONS.md | Mermaid diagram; deployment guide; every AD recorded; phase file written |

9 tasks. One branch, one commit per task, single PR at phase end.

---

## Task 8.1 — Per-tenant rate limiting

Protect the customer-facing webhook from abuse. Rate limits are per-tenant,
configurable per plan tier, and enforced in Redis — not in the DB.

**What ships:**
- `app/infra/rate_limiter.py`: `RateLimiter(redis_client, default_rpm)` class.
  `check_and_increment(tenant_id: UUID) -> RateLimitResult` — INCR +
  EXPIRE-on-first-write. Returns `{allowed: bool, current: int, limit: int,
  reset_at: datetime}`.
- `app/api/dependencies.py`: `rate_limit_check` FastAPI dependency. Reads
  tenant's `rate_limit_rpm` from `business_policies` (cached in Redis with a 60s
  TTL to avoid a DB hit per request). On limit exceeded: raises `HTTPException(429)`
  with body `{detail: "عذراً، لقد تجاوزت الحد المسموح به. حاول بعد قليل."}` and
  `Retry-After` header.
- `app/api/webhooks.py`: `rate_limit_check` added to the webhook endpoint's
  dependency list (alongside the existing `resolve_message_identity`).
- `Settings`: `RATE_LIMIT_DEFAULT_RPM: int = 30`.
- `tests/test_rate_limiter.py`: (a) tenant hits limit → 429 returned; (b) two
  tenants hit limit simultaneously → counters are independent; (c) counter resets
  after the window; (d) tenant with `rate_limit_rpm = 0` policy row is treated as
  "no limit" (bypass).

**DoD:** 429 returned on over-limit; `Retry-After` header present; two tenants
share no counter state (proven by test); structured log on every 429 with
`{tenant_id, endpoint, limit}`; `uv run pytest` green.

---

## Task 8.2 — Agent golden evals + red-team CI gate

Extend the Phase 7 routing golden evals to per-agent behavioural evals. Add a
red-team eval for injection/jailbreak attacks (GUARDRAILS.md Phase 8 item).

**What ships:**
- `app/agents/eval/golden/` directory with one JSONL file per agent:
  - `order.jsonl` — 20+ customer order queries in Lebanese Arabic
  - `inventory.jsonl` — 20+ low-stock / PO queries in Lebanese Arabic
  - `finance.jsonl` — 20+ revenue / anomaly queries in Lebanese Arabic
  - `customer.jsonl` — 20+ churn / re-engagement queries in Lebanese Arabic
  - `advisor.jsonl` — 20+ morning briefing / strategic queries in Lebanese Arabic
  - `redteam.jsonl` — 25+ injection, jailbreak, cross-tenant probe, off-topic
    abuse attempts (Arabic and Arabizi transliteration)
- Each entry format:
  `{query_ar, expected_intent, expected_tools: [str], notes, redteam?: bool}`
- `app/agents/eval/evaluate_agents.py`: loads each JSONL, runs the mock
  supervisor (same offline mock LLM from Phase 7), asserts per-agent: tool
  sequence match ≥ 0.85, response language is Arabic, no hallucinated product
  names in output. Red-team: `check_input()` block rate ≥ 0.92.
- `app/agents/eval/agent_thresholds.yaml`:
  ```yaml
  per_agent_tool_match_min: 0.85
  response_language_arabic_min: 1.00
  redteam_block_rate_min: 0.92
  ```
- CI (`ci.yml`): new step `Agent golden evals` runs
  `uv run python -m app.agents.eval.evaluate_agents`. Exits 1 on any threshold
  breach.
- `tests/test_agent_golden_evals.py`: proves the evaluator exits 1 when a mock
  agent returns wrong tools; proves it exits 0 on a passing mock.
- Every tripped red-team rail writes an `audit_log` entry (reuses `AuditService`
  from Phase 1) with `{tenant_id, action: "guardrail_trip", target: rail_name}`.

**DoD:** 120+ golden queries total (5 agents × 20+ + 25 red-team) committed in
JSONL; CI step green on the mock eval; regression test (break one agent's tool
sequence) fails CI; audit log entry written on every blocked injection; `uv run
pytest` green.

---

## Task 8.3 — Load test: 100 concurrent customers, 10 tenants, zero cross-tenant leak

Prove The Wall holds under load, not just in unit tests.

**What ships:**
- `tests/load/conftest.py`: seeds 10 tenants with 10 products each and 10
  customers each (100 customers total) using the async SQLAlchemy test session.
  Each tenant has a distinct WhatsApp number.
- `tests/load/test_concurrent_isolation.py`:
  - Creates an `httpx.AsyncClient` against the test app (FastAPI `TestClient`
    with a lifespan-compatible async transport, LLM mocked).
  - Fires 100 concurrent POST requests to `/webhooks/whatsapp`, each from a
    different (tenant, customer) pair, each requesting a valid product.
  - All 100 tasks run via `asyncio.gather`.
  - Assertion on every response: `response.json()["order"]["tenant_id"]` matches
    the tenant whose customer sent the request. No other tenant's `tenant_id`,
    product names, or customer IDs appear anywhere in the response body.
  - Also asserts HTTP 200 for all 100 requests (no server errors under load).
- Marked `@pytest.mark.load` — excluded from `pytest tests/` default run.
- `Makefile` or `scripts/run_load_test.sh`: one command to run the load suite.
- Runbook section: "How to run the load test before a production deploy."

**DoD:** 100 concurrent requests complete without error; zero cross-tenant data
in any response (asserted programmatically, not visually); test is repeatable and
deterministic (mock LLM, seeded DB); `pytest tests/load/ -m load` runs and passes;
instructions in RUNBOOK.md.

---

## Task 8.4 — Chaos tests: Vault / LLM / DB kill → graceful recovery

Simulate the three most likely production failures and assert the system
degrades gracefully, never catastrophically.

**What ships:**
- `tests/test_chaos.py` — three named test groups:

  **Group A — Vault kill:**
  - Patch `app.infra.vault.VaultClient.get_secret` to raise `VaultError`.
  - Call the `create_app()` lifespan entry point.
  - Assert: `SystemExit` is raised (or `RuntimeError` with a clear message
    containing "Vault"). Assert the structured log contains an ERROR line with
    `event = "vault.unavailable"`. The app does NOT start silently with empty
    secrets.

  **Group B — All LLM providers exhausted:**
  - Patch all three provider adapters (`GeminiRouter.tier1`, `GrokRouter.tier1`,
    `AnthropicRouter.tier1`) to raise `APIError`.
  - Call `supervisor.handle(message, tenant_id, session_id)`.
  - Assert: returns `LLMUnavailableResponse` (not a crash / unhandled exception).
  - Assert: the WhatsApp reply text contains an Arabic apology string (from
    `prompts/system_unavailable_ar.md`).
  - Assert: structured log has a `WARNING` line with `event = "llm.all_providers_failed"`.
  - Assert: a 200 HTTP response is returned by the webhook endpoint (the failure
    is handled, the webhook does not return 500).

  **Group C — Database disconnect:**
  - Patch `sqlalchemy.ext.asyncio.AsyncSession.execute` to raise
    `sqlalchemy.exc.OperationalError`.
  - GET `/health` — assert returns 200 (health does not hit the DB).
  - POST `/webhooks/whatsapp` — assert returns 503 (not 500); response body
    contains `{"detail": "service_unavailable"}`.
  - Assert structured log has `ERROR` line with `event = "db.connection_failed"`.

- Any missing graceful-handling code in `supervisor/agent.py` or
  `api/webhooks.py` discovered during testing is fixed in this task.
- `prompts/system_unavailable_ar.md`: the Lebanese Arabic message sent to
  customers when all LLMs are down. One file, used by both the supervisor and
  the dispatcher fallback.

**DoD:** all three chaos groups pass; no test relies on Docker or subprocess;
Vault kill produces `SystemExit` not a silent hang; all-LLM-kill returns
HTTP 200 to the webhook caller with Arabic message; DB kill returns HTTP 503;
`uv run pytest tests/test_chaos.py` green.

---

## Task 8.5 — Graceful degradation + manual order entry

Prove the ROADMAP DoD: "AI down, business still works." Abu Khaled can create
orders manually through the dashboard when the AI is unavailable.

**What ships:**

**Backend:**
- `app/api/orders.py`: new endpoint `POST /orders/manual`.
  - Auth: `get_current_user` (dashboard JWT, not webhook identity).
  - Body: `ManualOrderRequest(customer_phone: str, items: list[ManualOrderItem])`
    where `ManualOrderItem(product_id: UUID, quantity: int)`.
  - Validates product IDs exist and belong to tenant. Sets `order.source = "manual"`.
  - Writes `Order` + `OrderItem` rows through the existing `OrderService`.
  - Returns the created `Order` schema (same as the agent-created order).
  - No supervisor call, no LLM, no ActionGate needed (human IS the operator).
- `app/api/health.py` (new or extend): `GET /health/ai` — returns
  `{"available": true/false}`. Checks `app.state.llm_router.is_healthy()` (a
  simple flag set by the last successful/failed LLM call, stored in app state).
- `app/agents/llm/router.py`: `FallbackLLMRouter` gains `is_healthy() -> bool`
  that returns False if the last call raised `LLMUnavailable` within the last
  60 seconds (time-based circuit breaker). Resets to True on next successful call.
- `services/dispatcher.py`: `handle_owner` catches `LLMUnavailable` →
  returns the text from `prompts/system_unavailable_ar.md`.
- `tests/test_manual_order.py`: (a) authenticated dashboard user creates an order
  with valid products → order in DB with `source = "manual"`; (b) invalid product
  ID → 422; (c) product from another tenant → 404 (The Wall); (d) unauthenticated
  request → 401.

**Frontend:**
- `frontend/src/pages/ManualOrderPage.tsx`: form with customer phone input,
  product multi-selector (fetched from the existing products API), quantity
  inputs. Submit → POST `/orders/manual`. On success: shows confirmation with
  order ID. Lebanese Arabic labels.
- `frontend/src/components/AIStatusBanner.tsx`: polls `/health/ai` every 30s.
  When `available == false`: shows a sticky banner in Arabic —
  "خدمة الذكاء الاصطناعي غير متاحة مؤقتاً — يمكنك إدخال الطلبات يدوياً".
  Banner links to the Manual Order page.
- Nav sidebar: "طلب يدوي" link visible only when `available == false`
  (or always, developer option).

**DoD:** `POST /orders/manual` creates an order scoped to tenant; cross-tenant
product rejected; `GET /health/ai` returns false when LLM is mocked to fail;
frontend banner appears when health endpoint returns false; manual order visible
in the order feed; `uv run pytest tests/test_manual_order.py` green; frontend
builds green.

---

## Task 8.6 — Log aggregator: Grafana Loki in docker-compose

Deliver constitution III's "logs are queryable" requirement for a running stack.

**What ships:**
- `docker-compose.yml`: add `loki` and `grafana` services under the
  `observability` profile:
  ```yaml
  loki:
    image: grafana/loki:2.9.0
    profiles: [observability]
    ports: ["3100:3100"]
    volumes: ["loki_data:/loki"]
  grafana:
    image: grafana/grafana:10.2.0
    profiles: [observability]
    ports: ["3000:3000"]
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
    volumes:
      - "./infra/grafana/provisioning:/etc/grafana/provisioning"
      - "./infra/grafana/dashboards:/var/lib/grafana/dashboards"
  ```
- `app/infra/logging/loki_handler.py`: a structlog processor (runs after the
  existing JSON renderer). In a daemon thread: buffers log lines and periodically
  POSTs them to `http://loki:3100/loki/api/v1/push` with labels
  `{app: "modir", tenant_id: ..., level: ...}`. Falls back silently if Loki is
  unreachable (observability must never break the app). Configured from
  `Settings.LOKI_URL` (optional — empty string disables it).
- `app/main.py`: wire `LokiHandler` into the structlog processor chain if
  `settings.loki_url` is set.
- `infra/grafana/provisioning/datasources/loki.yaml`: auto-provision Loki as
  datasource.
- `infra/grafana/provisioning/dashboards/modir.yaml`: auto-provision dashboard
  directory.
- `infra/grafana/dashboards/modir.json`: pre-built dashboard with panels:
  - Request rate by tenant (last 1h)
  - Error rate (4xx / 5xx split)
  - LLM fallback activations per hour
  - Rate limit hits per tenant
  - Cost per tenant per day (from `agent_runs` log lines)
  - Agent latency (p50 / p95)
- README section: "Observability stack — how to run Loki + Grafana."

**DoD:** `docker compose --profile observability up` starts without error; a
structured log line from the API appears queryable in the Grafana Explore view
within 10 seconds; Loki unreachable does NOT crash the API (test: start API
without the observability profile, run a request, no error); `uv run pytest` green.

---

## Task 8.7 — Cost dashboards + threshold alerts

Give Abu Khaled and the founder visibility into LLM spend, with automatic alerts.

**What ships:**

**Backend:**
- `business_policies` rows: on tenant creation (or via a one-time migration
  helper), insert `daily_llm_budget_usd = "0"` and `alert_webhook_url = ""`
  as default policy rows for each tenant. A new helper in
  `services/business_profile.py`: `ensure_default_policies(tenant_id)`.
- `app/api/admin.py` (extend): `GET /admin/costs/summary` — returns
  `{today_usd, budget_usd, percentage, alert_triggered: bool}` for a tenant.
  Reuses `AgentRunRepository.daily_summary`.
- `app/api/costs.py` (new owner-facing): `GET /dashboard/costs` (no admin role
  required, scoped to current tenant via JWT). Returns 30-day daily cost array
  with per-agent breakdown. Powers the frontend cost chart.
- `app/worker.py` (extend): `CostAlertTask` runs every 60 minutes in the
  worker loop. Queries each tenant's today cost vs `daily_llm_budget_usd`. If
  exceeded and `alert_webhook_url` is set and last alert was > 1h ago: POST JSON
  `{tenant_id, today_usd, budget_usd, message}` to the webhook URL via
  `httpx.AsyncClient`. Failures are logged, not raised.
- `tests/test_cost_alerts.py`: (a) budget exceeded + webhook URL set → POST
  fired; (b) budget = 0 → no alert (no limit); (c) two tenants — only the over-
  budget one fires; (d) last alert < 1h ago → no duplicate alert.

**Frontend:**
- `frontend/src/pages/CostDashboardPage.tsx`: bar chart (date × cost_usd), per-
  agent colour split, budget line, today's total. Arabic labels. Reads from
  `GET /dashboard/costs`.
- Nav sidebar: "التكاليف" (Costs) entry.
- `npm run lint && npm run typecheck && npm run build` green.

**DoD:** `GET /dashboard/costs` returns 30-day data scoped to tenant; budget
exceeded → webhook POST within 1h; no duplicate alerts; frontend page shows
chart; cross-tenant: owner sees only their own costs; `uv run pytest
tests/test_cost_alerts.py` green.

---

## Task 8.8 — Backup + restore scripts + RUNBOOK.md

Prove the ROADMAP DoD: "Restore from backup completes in under 15 minutes."

**What ships:**
- `scripts/backup.sh`:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  TS=$(date +%Y%m%d_%H%M%S)
  pg_dump "$DATABASE_URL" | gzip > "backups/postgres/${TS}.sql.gz"
  mc mirror minio/uploads "backups/minio/${TS}/"
  echo "Backup complete: ${TS}"
  ```
  Requires `pg_dump` and `mc` (MinIO client). Both available in the api image.
- `scripts/restore.sh`:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  LATEST=$(ls -t backups/postgres/*.sql.gz | head -1)
  gunzip -c "$LATEST" | psql "$DATABASE_URL"
  LATEST_MINIO=$(ls -td backups/minio/*/ | head -1)
  mc mirror "$LATEST_MINIO" minio/uploads
  echo "Restore complete from: $LATEST"
  ```
- `docker-compose.yml`: `backup` service (no profile — runs on demand):
  ```yaml
  backup:
    image: modir-backend
    command: /app/scripts/backup.sh
    profiles: [backup]
    volumes: ["./backups:/app/backups"]
    environment: [DATABASE_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY]
  ```
- `backups/` directory created in repo with a `.gitkeep` and added to
  `.gitignore` (backups are not versioned).
- **Prove the DoD**: run `backup.sh`, then `restore.sh` on the local dev stack.
  Record the elapsed time in `RUNBOOK.md`. Must be ≤ 15 minutes.
- `RUNBOOK.md` (repo root): covers all six failure scenarios:
  1. Vault is down at startup
  2. All LLM providers fail mid-request
  3. Postgres connection lost
  4. Redis connection lost (rate limiter unavailable — fall back to allow-all)
  5. MinIO connection lost (OCR upload fails — queue for retry)
  6. Worker crashes (cost alerts + OCR worker stop — jobs replay on restart)
  Each section: symptoms, immediate action, recovery command.

**DoD:** `scripts/backup.sh` runs without error on the dev stack; `scripts/
restore.sh` restores data verifiably (spot-check: count orders matches pre-
backup); end-to-end restore proven ≤ 15 min (time logged in RUNBOOK.md);
`RUNBOOK.md` covers all six failure scenarios; `docker compose --profile backup
run backup` works.

---

## Task 8.9 — README + architecture diagram + DECISIONS.md + PHASE_8 memory

The final task. Every architectural choice documented; one-command clone-and-run.

**What ships:**
- `README.md` (create at repo root if absent, or replace):
  - Project description (one paragraph, in English)
  - Mermaid architecture diagram showing the full system:
    WhatsApp → webhook → dispatcher → (customer path: OrderAgent; owner path:
    Supervisor → 5 agents) → Postgres / Redis / MinIO / Vault + Loki + Grafana
  - Prerequisites: Docker, docker compose, git
  - Quick start: `git clone && cp .env.example .env && docker compose up`
  - How to run tests: `uv run pytest -m "not integration and not load"`
  - How to run the observability stack: `docker compose --profile observability up`
  - How to run a backup: `docker compose --profile backup run backup`
  - Link to `RUNBOOK.md` for failure playbooks
  - Link to `DECISIONS.md` for architecture rationale
  - Link to `docs/DEFEND_IT.md` for the defend-it Q&A (local, gitignored)
- `DECISIONS.md` (repo root): one section per architecture decision, AD-0.x
  through AD-8.x. Each entry: decision, alternatives considered, reason chosen,
  date. Phase 8 adds AD-8.1 through AD-8.8 (as defined above). Earlier phases'
  decisions are recorded from memory / PHASE files.
- `tests/test_phase8_ci_guards.py`:
  - `GET /health/ai` endpoint exists and returns a JSON body with `available` key.
  - `POST /orders/manual` endpoint exists and requires auth.
  - `RateLimiter` constructor accepts a `default_rpm` argument.
  - `FallbackLLMRouter.is_healthy()` method exists.
  - `CostAlertTask` class exists in `app.worker`.
  - Red-team golden set has ≥ 25 entries.
  - Each per-agent golden set has ≥ 20 entries.
- `.specify/memory/phases/PHASE_8.md`: this file (already written — no action
  needed at Task 8.9, it was written before Task 8.1).

**DoD:** README renders correctly on GitHub (Mermaid diagram visible); `git clone
&& cp .env.example .env && docker compose up && curl localhost:8000/health` works
on a fresh machine; DECISIONS.md is complete through Phase 8; `tests/
test_phase8_ci_guards.py` all pass; `uv run pytest` green; CI green on push.

---

## Phase 8 — Definition of Done

- [ ] CI: all 5 agent golden evals run on every push; a regression fails the build
- [ ] CI: red-team injection set block rate ≥ 0.92; regression fails the build
- [ ] Rate limit: 429 returned on over-limit; two tenants have independent counters
- [ ] Vault kill: API refuses to start with a clear error (not a hang, not a 500)
- [ ] All LLM providers killed: supervisor returns graceful Arabic reply, HTTP 200
- [ ] DB kill: affected endpoints return 503, not 500
- [ ] Load test: 100 concurrent requests / 10 tenants — zero cross-tenant data in
  any response (asserted, not visually checked)
- [ ] Restore from backup completes in ≤ 15 minutes (proven once, time in RUNBOOK.md)
- [ ] "AI down" demo: manual order created through dashboard; order appears in DB
  with `source = "manual"`; AI status banner visible in frontend
- [ ] Cost alert fires within 1h of a budget being exceeded (tested with mock)
- [ ] Grafana Loki receives log lines from the API (proven with `docker compose
  --profile observability up`)
- [ ] README renders; `git clone + docker compose up + curl /health` works
- [ ] DECISIONS.md covers every AD from Phase 0 through Phase 8
- [ ] `uv run pytest -m "not integration and not load"` green
- [ ] CI green on push to `feature/MOD-8-hardening`

---

## Phase 8 — Defend-it questions

- What happens when Gemini, Grok, and Claude are all down simultaneously?
  Walk me through every log line and every HTTP response in that scenario.
- Show me the rate limiter key for tenant A. What prevents tenant B's requests
  from affecting tenant A's counter?
- Our red-team eval blocks 94% of injection attempts. What are the 6% that get
  through, and why is Layer 1 (The Wall) still the real safety boundary?
- Walk me through a restore: which commands, in which order, and how do you
  verify the data is intact?
- The load test fires 100 requests. How does the isolation assertion work?
  What does it check, exactly?
- A tenant's LLM budget is exceeded at 11pm. When is the alert sent? What if
  the `alert_webhook_url` is unreachable?
- The Loki handler fails to POST logs because Loki is down. What happens to
  the API request that triggered the log? What happens to the log line?
- Abu Khaled's shop has 0 AI — all providers are down. Walk me through the
  full experience: what does the customer see, what does Abu Khaled see, and
  what actions can Abu Khaled still take?
- Why `pg_dump` and not `pgbackrest` or WAL-shipping?
- How would you add a seventh container to the chaos tests without changing the
  test structure?

---

## Ready for Phase 9?

You are ready when:
- Every checkbox in the Definition of Done is checked.
- `uv run pytest -m "not integration and not load"` is green.
- The load test has been run once manually and passed.
- `scripts/restore.sh` has been run once and the elapsed time is in RUNBOOK.md.
- An end-to-end demo shows: all LLMs mocked to fail → Arabic "unavailable"
  reply → Abu Khaled uses manual order entry → order in DB → cost dashboard
  shows today's spend.
- You can answer every defend-it question above out loud, without notes.

Phase 9 is Polish, Demo, and Documentation: a public README, a demo video of
the full Lebanese Arabic flow, DECISIONS.md as a portfolio artifact, and
optional deployment to Railway/Fly.io for a live demo URL.
