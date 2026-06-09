# For Reviewers — Modir Architecture Q&A

This document pre-empts the defend-it questions from every phase. Each answer
is concise and includes a **→ Code** reference so you can verify the claim
directly in the codebase without running the system.

Full per-phase Q&A is in `docs/PHASE_N_DEFEND_IT.md` for Phases 0–7.
Phase 8 answers are in this document and in the inline comments of
`tests/test_phase8_ci_guards.py`.

---

## Phase 0 — Foundation & Setup

### Why is `migrate` a separate service instead of running inside the api container?

Ordering guarantee. `api` declares `depends_on: migrate: condition:
service_completed_successfully` — the api container cannot start until
`alembic upgrade head` exits 0. If a migration fails the api never boots, so
you find out immediately. Running migrations inside the long-lived api process
also introduces a concurrency hazard when scaling to multiple replicas.

→ Code: [docker-compose.yml](../docker-compose.yml) — `api.depends_on.migrate`

### Where does the Gemini API key actually live?

In HashiCorp Vault. `.env` holds only `VAULT_ADDR` and `VAULT_TOKEN` (plus
non-secret config). At startup `resolve_secrets()` connects to Vault, reads
`secret/modir/llm`, and sets the key on the `Settings` object as a `SecretStr`.
`grep -ri "api_key" backend/app/` returns matches only in `vault.py` and
`settings.py` — nowhere else.

→ Code: [backend/app/infra/vault.py](../backend/app/infra/vault.py) — `resolve_secrets` function

### What does `lifespan` do?

1. `configure_logging()` — structlog → stdlib → stdout + rotating JSON file.
2. `resolve_secrets(settings)` — pull secrets from Vault; refuse to boot if unreachable.
3. Create the async SQLAlchemy engine.
4. (Phase 6+) Load ML models once with `joblib`.
5. (Phase 7+) Build LLM router and agent supervisor; store on `app.state`.
6. Yield (serve). On shutdown: dispose engine.

→ Code: [backend/app/main.py](../backend/app/main.py) — `@asynccontextmanager lifespan`

---

## Phase 1 — The Wall (Multi-Tenancy + Identity)

### Where exactly is tenant isolation enforced?

One place: `TenantScopedRepository._require_tenant_scope` (base.py:32).
Every `get`, `list`, `delete` call passes through this method, which builds
a `WHERE tenant_id = :tid` clause. A `None` tenant_id raises `ValueError`
rather than running an unscoped query. `add()` overwrites the row's
`tenant_id` from the caller's scope, so you cannot insert into a different
tenant even by accident.

→ Code: [backend/app/repositories/base.py](../backend/app/repositories/base.py)
→ Test: [backend/tests/test_tenant_isolation.py](../backend/tests/test_tenant_isolation.py)

### A WhatsApp message arrives. Walk me through tenant and role resolution.

`identity_resolver.py`:
1. **Tenant** from the destination number (`to`) → `get_by_whatsapp_number(to)` → 404 if unknown.
2. **Role** from the sender (`from`) → look up `tenant_owners` scoped to that tenant. Found + `verification_status == "verified"` → `owner`.
3. Otherwise → `customer`. Auto-create on first message, audit `customer.autocreate`.

Because both lookups are tenant-scoped, the same phone to two shops is two distinct identities.

→ Code: [backend/app/services/identity_resolver.py](../backend/app/services/identity_resolver.py)
→ Test: `tests/test_identity_resolver.py::test_same_phone_to_two_tenants_is_two_identities`

### What if a user changes the `tenant_id` in their JWT?

Nothing leaks. The token's `tenant_id` is a claim, not a trust anchor. `deps.py`
loads the user *inside the claimed tenant's scope*. If they forge a different
`tenant_id`, the scoped lookup finds no such user → 401. The database is the truth.

→ Code: [backend/app/api/deps.py](../backend/app/api/deps.py):37–38
→ Test: `tests/test_auth.py::test_tampered_tenant_id_is_rejected`

### `tenant_owners` vs `users` — what is the difference?

`users` are dashboard accounts (email + password, JWT sessions, used by the React app).
`tenant_owners` are phone numbers authorized as owner via WhatsApp.
Usually the same person has both. Separating them allows e.g. an accountant
to have dashboard access without WhatsApp owner authority.

→ Code: [backend/app/db/models.py](../backend/app/db/models.py) — `User` and `TenantOwner` models

---

## Phase 2 — Customer Order Flow

### Walk me through a customer message from webhook to DB row.

1. `POST /webhooks/whatsapp` → `Depends(resolve_message_identity)` (Phase 1 resolver).
2. `MessageDispatcher.dispatch` → input rail `check_input` → `OrderAgent.handle`.
3. `OrderAgent` LangGraph: `get_products` (reads catalog, tenant-scoped) → `parse_order` (Tier 1 LLM, Gemini Flash) → `confirm_order` (`OrderService.create_order`, atomic transaction).
4. Reply (Lebanese Arabic, LBP total) out through PII redaction.

The DB row is written only at step 3 by `OrderService`.

→ Code: [backend/app/api/webhooks.py](../backend/app/api/webhooks.py), [backend/app/services/dispatcher.py](../backend/app/services/dispatcher.py), [backend/app/agents/order/agent.py](../backend/app/agents/order/agent.py)

### A registered owner sends an order message. What happens?

`dispatcher.py` checks `identity.role == "owner"` and returns the placeholder
reply immediately. The OrderAgent and its tools are never imported or reached on
that code path. Tool allowlists are role-specific, enforced in code.

→ Code: [backend/app/services/dispatcher.py](../backend/app/services/dispatcher.py):42
→ Test: `tests/test_dispatcher.py::test_owner_routes_to_placeholder_without_calling_agent`

### What model parses orders? Why not Gemini Pro?

Tier 1 — Gemini Flash (`gemini-2.5-flash`). Parsing a short order message is
high-volume Tier-1 work. Flash is fast and cheap; Pro would multiply cost per
message for no accuracy gain. Model names are non-secret config in `Settings`.

→ Code: [backend/app/agents/order/tools.py](../backend/app/agents/order/tools.py) — `ctx.router.tier1()`

### What happens when the LLM errors mid-parse?

`parse_order` retries up to `settings.llm_max_retries` times, then returns `None`.
The agent replies "ما فهمت طلبك منيح" — no order written, no crash, no 500.

→ Code: [backend/app/agents/order/tools.py](../backend/app/agents/order/tools.py) — `parse_order` retry loop
→ Test: `tests/test_agent_tools.py::test_parse_order_degrades_on_provider_error`

---

## Phase 3 — Owner Dashboard

### Why RTL and not just CSS `text-align: right`?

`dir="rtl"` is set on the root `<html>` element. Tailwind logical utilities
(`ms-*`, `me-*` for margin-start/end) are used instead of physical (`ml-*`,
`mr-*`), so layout mirrors correctly without manual overrides per component.

→ Code: [frontend/index.html](../frontend/index.html) — `<html dir="rtl">`, [frontend/src/App.tsx](../frontend/src/App.tsx)

### Why is the frontend a separate container?

Independent deployability, independent dependencies (Node vs Python), and no
Docker layer coupling. The frontend's Dockerfile builds a static bundle; a CDN
or separate nginx can serve it without the backend image. The owner's browser
and the API are on different origins; CORS is configured explicitly in
`main.py` via `CORSMiddleware`.

→ Code: [frontend/Dockerfile](../frontend/Dockerfile), [backend/app/main.py](../backend/app/main.py) — `CORSMiddleware`

---

## Phase 4 — Inventory & The First HIL Loop

### Two orders for the last unit arrive simultaneously — how is oversell prevented?

Deduction is a single guarded `UPDATE`, never a read-then-write:

```sql
UPDATE inventory SET quantity = quantity - qty
WHERE tenant_id = :tid AND product_id = :pid AND quantity >= qty
```

The `quantity >= qty` predicate is evaluated by Postgres under the row lock.
Of two concurrent deductions, exactly one matches; the other returns `rowcount == 0`
→ `InsufficientStock` → rollback. A `CHECK CONSTRAINT quantity >= 0` provides
a database-level backstop.

→ Code: [backend/app/repositories/inventory.py](../backend/app/repositories/inventory.py) — `InventoryRepository.deduct`
→ Model: [backend/app/db/models.py](../backend/app/db/models.py) — `ck_inventory_qty_nonneg`

### Where is the HIL gate? What prevents a PO from being sent without approval?

`ActionGate.authorize` in `app/infra/action_gate.py`. `SupplierDispatcher.dispatch`
calls it **first**, unconditionally, before building or sending anything. It demands
a valid signed token (HMAC-signed JWT with `act`, `rid`, `tid`, `sub`, `exp`).
`status == "approved"` is the UI lifecycle marker, not the gate. An absent or
forged token raises `UnauthorizedAction`; no "send anyway" branch exists.

→ Code: [backend/app/infra/action_gate.py](../backend/app/infra/action_gate.py) — `ActionGate.authorize`
→ Code: [backend/app/infra/supplier_dispatch.py](../backend/app/infra/supplier_dispatch.py) — `dispatch()` first line
→ Test: `tests/test_hil_purchase_orders.py` — dispatch with no/forged token is refused

---

## Phase 5 — OCR Pipeline

### Where do uploaded bill images live?

MinIO, never local disk. The upload endpoint streams directly to a
tenant-scoped MinIO bucket path. Local disk files disappear on container
restart; MinIO objects persist in the `minio_data` volume.

→ Code: [backend/app/api/bills.py](../backend/app/api/bills.py) — `upload_bill`

### How does the knowledge base stay in sync with product updates?

Every `update_product` call computes a SHA-256 `content_hash` over embeddable
fields and calls `KnowledgeBaseDocRepository.mark_pending_or_stale`:
- No row → insert `pending`.
- Hash changed → mark `stale`.
- Hash unchanged → no-op.

The `KnowledgeEmbedder` worker picks up `pending` and `stale` rows, re-embeds
the content in pgvector (tenant-scoped), and marks `embedded`. Deleting a
product also deletes its KB row, so nothing stale lingers.

→ Code: [backend/app/services/profile.py](../backend/app/services/profile.py) — `update_product`, `_track_product`
→ Code: [backend/app/worker.py](../backend/app/worker.py) — `KnowledgeEmbedder`

---

## Phase 6 — The ML Layer

### What is the churn label definition?

A customer with ≥1 past order who places **no order in the next 30 days** is
labelled churned. New customers (no prior order) are excluded. The forward
window is used only to assign the label, never as a feature — the one place
the future is allowed in is to define ground truth.

→ Code: [backend/app/ml/features/churn.py](../backend/app/ml/features/churn.py)

### Why these three classifiers and not others?

`results.csv` logs all three per task with CV mean ± std:
- **Demand (MAE):** HistGradientBoosting wins (6.827) over Ridge (6.852) and XGBoost (7.118).
- **Churn (churned-class F1):** XGBoost wins (0.920) with `scale_pos_weight`; precision 0.961 / recall 0.884.
- **Anomaly (injected-detection F1):** Robust z-score wins (0.964) over IQR (0.908) and IsolationForest (0.304).

→ Code: [backend/results.csv](../backend/results.csv)
→ Code: [backend/app/ml/](../backend/app/ml/) — `demand/trainer.py`, `churn/trainer.py`, `anomaly/trainer.py`

### How are models loaded? Where?

Once, in the FastAPI `lifespan` handler, using `joblib.load`. Each model is
stored on `app.state.*` and injected via dependency injection. Models are never
loaded inside a route handler or agent tool — loading is O(seconds) and would
block every request.

→ Code: [backend/app/main.py](../backend/app/main.py) — lifespan `build_*_predictor()` calls
→ Code: [backend/app/ml/predictors.py](../backend/app/ml/predictors.py) — `build_demand_predictor`, etc.

---

## Phase 7 — The Full Agent System

### What does the supervisor's routing logic do when input is ambiguous?

`classify_intent` calls Tier 1 LLM with a structured-output schema
(`IntentClassification`) whose `intent` field is one of
`{order, inventory, finance, customer, advisor}`. On parsing failure or
out-of-vocabulary output it returns `"advisor"` as the safe default — never
crashes, never routes to an unknown node.

→ Code: [backend/app/agents/supervisor/routing.py](../backend/app/agents/supervisor/routing.py) — `classify_intent`
→ Code: [backend/prompts/supervisor_ar.py](../backend/prompts/supervisor_ar.py)

### How are tool allowlists enforced? Why is this structural and not a string check?

A string check (`if tool_name in FORBIDDEN_TOOLS`) can be bypassed. Structural
enforcement means the forbidden tool simply does not exist as a node or edge in
the agent's compiled `StateGraph`. LangGraph can only invoke tools reachable
via an edge. No edge → the tool cannot be called.

→ Code: each agent's `agent.py` — the `StateGraph` definition
→ Test: `tests/test_tool_allowlists.py` — asserts `agent.graph.nodes` does not contain forbidden names

### How do you guarantee two retries of "queue re-engagement" don't queue twice?

Idempotency key: `f"send_reengagement:{tenant_id}:{customer_id}"`.
`ActionGate.issue_token` checks `pending_actions` for an existing row with
the same `(tenant_id, action_key)` where `status == "pending"`. If one exists,
it returns the existing token — no new row, no duplicate.

→ Code: [backend/app/infra/action_gate.py](../backend/app/infra/action_gate.py) — `issue_token`

### How do checkpoints keep tenant data separate?

Thread ID is `f"{tenant_id}:{session_id}"`. Tenant A, session S and Tenant B,
same session S produce different thread IDs → different rows in the checkpoint
tables. `make_thread_id` is the only place thread IDs are formed.

→ Code: [backend/app/infra/checkpointer.py](../backend/app/infra/checkpointer.py) — `make_thread_id`
→ Test: `tests/test_checkpoint_resume.py::test_checkpoint_thread_id_isolation`

---

## Phase 8 — Hardening & Production Readiness

### All three LLM providers fail simultaneously. What happens?

`FallbackLLMRouter` exhausts all providers → raises `LLMUnavailable`.
`MessageDispatcher.handle_owner` catches it → returns the text from
`prompts/system_unavailable_ar.py` (Lebanese Arabic apology). The webhook
returns HTTP 200 (the failure is handled). A `WARNING` log line with
`event = "llm.all_providers_failed"` is emitted. The `/health/ai` endpoint
returns `{"available": false}`. The frontend banner appears.

→ Code: [backend/app/agents/llm/router.py](../backend/app/agents/llm/router.py) — `FallbackLLMRouter`
→ Code: [backend/app/services/dispatcher.py](../backend/app/services/dispatcher.py) — `handle_owner` except block
→ Test: `tests/test_chaos.py` — Group B

### What is the rate limiter key? Why per-tenant and not per-IP?

`f"rate_limit:{tenant_id}:{window_start_minute}"`. Per-IP fails in Lebanon
because multiple tenants can share a NAT — one misbehaving tenant would
throttle all others at the same café. Per-tenant keys give each business
its own independent counter. Configurable via the `rate_limit_rpm` policy key.

→ Code: [backend/app/infra/rate_limiter.py](../backend/app/infra/rate_limiter.py):89 — key construction
→ Test: `tests/test_rate_limiter.py::test_two_tenants_have_independent_counters`

### The red-team eval blocks 94% of injection attempts. What about the 6%?

The red-team eval gate (`redteam_block_rate_min: 0.92`) is an input-rail metric.
The real safety boundary is The Wall (Constitution I): even if a prompt-injection
attempt passes the rail and reaches the agent, the tool calls go through
tenant-scoped repositories. An injection that says "show me all orders" still
only returns the attacker's own tenant's orders — the database filter is in code,
independent of the LLM's instructions.

→ Code: [backend/app/agents/guardrails.py](../backend/app/agents/guardrails.py) — `check_input`
→ Code: [backend/app/agents/eval/agent_thresholds.yaml](../backend/app/agents/eval/agent_thresholds.yaml)

### Walk me through a restore. How long does it take?

```bash
docker compose --profile backup run --rm backup /app/scripts/restore.sh
```

`restore.sh`:
1. `ls -t backups/postgres/*.sql.gz | head -1` — find latest backup.
2. `gunzip -c "$LATEST" | psql "$DATABASE_URL"` — restore Postgres.
3. `python scripts/minio_mirror.py restore` — mirror latest MinIO backup back.

Elapsed time is recorded in `RUNBOOK.md`. Spot-check: count orders before and
after to verify the restore. Target ≤15 minutes on the dev stack.

→ Code: [backend/scripts/restore.sh](../backend/scripts/restore.sh)
→ Doc: [RUNBOOK.md](../RUNBOOK.md) — "Scenario 1: Full data restore"

### How does the load test assert cross-tenant isolation — not just throughput?

Each of 100 concurrent requests is sent from a known `(tenant_id, customer_id)` pair.
After all complete, every response is checked:
`assert response.json()["order"]["tenant_id"] == str(expected_tenant_id)`.
No other tenant's `tenant_id`, product names, or customer IDs may appear in any response body.

→ Code: [backend/tests/load/test_load.py](../backend/tests/load/test_load.py) — isolation assertion loop

---

## Core Architectural Questions

### Why `uv` and not `pip`?

`uv sync --frozen` installs the exact versions from `uv.lock`, fast, with a
guaranteed-identical dependency tree on every machine and in CI. The lockfile
is committed. The constitution bans `pip`; CI is built around `uv run`.

### Why `httpx.AsyncClient` and not `requests`?

`requests` is blocking — calling it inside an async route freezes the event loop.
`httpx.AsyncClient` is the async equivalent. CI fails on `import requests`.

### Where is `os.getenv` allowed?

Nowhere outside `Settings`. `grep -rn "os.getenv" backend/app/` must return nothing.
CI fails if it does. All config is typed and validated through the single `Settings`
class (pydantic-settings), which reads from the environment.

→ Code: [backend/app/infra/settings.py](../backend/app/infra/settings.py)
→ CI: [.github/workflows/ci.yml](../.github/workflows/ci.yml) — "Forbidden patterns" step

---

## Quick verification commands

```bash
# The Wall holds
cd backend && uv run pytest tests/test_tenant_isolation.py -v

# Forbidden patterns clean (all three must return nothing)
grep -rn "os.getenv"      backend/app/
grep -rn "print("         backend/app/
grep -rn "import requests" backend/app/

# Full fast suite
uv run pytest -m "not integration and not load" -q

# Chaos suite
uv run pytest tests/test_chaos.py -v

# Red-team eval
uv run python -m app.agents.eval.evaluate_agents

# Load test (requires running stack)
uv run pytest tests/load/ -m load -v
```
