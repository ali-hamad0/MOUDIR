# Phase 7 — The Full Agent System

> All five specialist agents wired into a LangGraph supervisor. The supervisor
> routes owner messages to the right agent; each agent calls the Phase 6 ML
> models and the Phase 5 RAG as tools — it does not retrain them. By the end
> of this phase, Abu Khaled can type a question in Lebanese Arabic and Modir
> routes it to the right specialist, pulls live data, calls ML predictions,
> and replies — resuming mid-conversation from a Postgres checkpoint if
> anything restarts.

Read `.specify/memory/constitution.md` FIRST (Principles I, III, IV, V all
apply heavily here). Then read `.specify/memory/ROADMAP.md` (Phase 7 section).
Then this file. Work task by task; pause for approval after each task before
committing.

---

## Goal

One LangGraph supervisor that:

1. Receives an owner WhatsApp message (identity already resolved by Phase 1)
2. Classifies intent and routes to exactly one specialist sub-graph
3. Each specialist calls live data tools, ML predictors (Phase 6), and/or RAG
   (Phase 5) — Constitution IV strictly in force (ML predicts, LLM explains)
4. Returns a Lebanese Arabic reply and persists the checkpoint to Postgres
5. Resumes from that checkpoint if the container restarts mid-run

Five specialist agents, three new:

| Agent | Phase | What it does |
|-------|-------|-------------|
| **OrderAgent** | 2 (existing) | Customer order flow — integrate under supervisor |
| **InventoryAgent** | 4 (existing) | Low-stock detection, PO drafting (HIL L2) |
| **FinanceAgent** | **7 (new)** | Revenue summary, anomaly explanation |
| **CustomerAgent** | **7 (new)** | Churn re-engagement draft (HIL L2) |
| **AdvisorAgent** | **7 (new)** | Morning briefing, strategic synthesis |

---

## Resist scope creep

- **No model retraining.** Phase 6 models load from their `.joblib` artifacts;
  this phase CALLS them. No new `train_*.py` files.
- **No new ML models.** The three Phase 6 models are the ML layer. Any numeric
  prediction in Phase 7 comes from those models or live DB queries, never from
  an LLM directly (Constitution IV).
- **No new OCR work.** BillExtractionAgent (Phase 5) is infrastructure; Phase 7
  does not modify it.
- **No financial writes.** FinanceAgent is read-only in Phase 7. It reads revenue
  data and explains anomalies — it does not create ledger entries or expense
  records. That is Phase 8+.
- **No customer re-engagement send.** CustomerAgent drafts the message and queues
  it for HIL approval. Actual WhatsApp send is Phase 10 (Meta API). Phase 7
  queues to the approvals inbox and stops there.
- **Frontend is minimal.** A basic owner chat panel that sends a message and shows
  the response (Task 7.13). No conversation history persistence in the UI, no
  agent-selector UI, no cost chart. The API is the deliverable.

---

## Prerequisites (read before Task 7.1)

- **Phase 6 is complete and merged to main.** Three models load via lifespan
  (`app.state.demand_predictor`, `churn_predictor`, `anomaly_detector`).
  `/predictions/*` serve tenant-scoped predictions. `uv run pytest` is green.
- **Phase 5 RAG corpora are live.** Both knowledge and bills pgvector corpora
  exist. `search_knowledge_base` tool works in the OrderAgent.
- **Phase 4 HIL gate is the pattern.** `app.infra.action_gate` + the approvals
  inbox + `app.services.approvals` is how Phase 7 queues Level-2 actions (PO
  draft + customer re-engagement). Do not build a second gate.
- **The owner dispatch path is a placeholder.** `services/dispatcher.py` returns
  `OWNER_PLACEHOLDER` for all owner messages. Task 7.7 replaces this with the
  supervisor.
- **LLM router is Gemini-only today.** `agents/llm/router.py` has `GeminiRouter`.
  Task 7.1 extends it to Gemini → Grok → Claude Haiku fallback.

---

## Core constraint reminders

### Constitution I — The Wall

- Postgres checkpoints are keyed `f"{tenant_id}:{session_id}"`. Thread IDs
  MUST be tenant-prefixed. A checkpoint for Tenant A must never be readable by
  Tenant B. Test it.
- Every new tool that reads the DB passes `tenant_id` to its repository method.
  No raw SQL in tools. The Wall does not stop at Phase 6.
- `agent_runs` cost rows carry `tenant_id`. Admin endpoint filters by it.

### Constitution III — Observability

- Every supervisor routing decision is a structured log line:
  `tenant_id, intent, routed_to, model_tier, latency_ms`.
- Every LLM fallback activation: `tenant_id, provider_from, provider_to, reason`.
- Every HIL action queued: `tenant_id, action_type, actor, queued_at`.
- `print()` is banned. CI fails on it.

### Constitution IV — ML predicts, LLM explains

- FinanceAgent: AnomalyDetector (Phase 6) produces `is_anomalous`. The LLM
  then EXPLAINS it in Lebanese Arabic. The LLM never produces the anomaly flag.
- AdvisorAgent: ChurnPredictor → risk scores; DemandPredictor → demand numbers;
  AnomalyDetector → anomaly flags. The LLM synthesizes all three into a briefing.
  The LLM never produces the numbers.
- CustomerAgent: ChurnPredictor → ranked at-risk customers. The LLM drafts the
  re-engagement message. The LLM never decides who is at risk.

### Constitution V — HIL is architecturally enforced

- CustomerAgent's `queue_reengagement` tool is Level 2. It writes a pending
  action to the approvals inbox and returns the approval URL. It does NOT send
  the message. The send is downstream of human approval.
- The existing `ActionGate` in `app.infra.action_gate` is the gate. No new gate.
- The same `app.services.approvals` inbox the owner already uses for POs shows
  re-engagement drafts too. One inbox, one UI, one pattern.

---

## Architecture decisions (recorded — belong in docs/DECISIONS.md at Task 7.14)

### AD-7.1 — LLM Router: FallbackLLMRouter replaces GeminiRouter

`agents/llm/router.py` gains a `FallbackLLMRouter` that wraps the ordered
provider list `[GeminiFlash, Grok, ClaudeHaiku]`. On any rate-limit, timeout,
or API error, it tries the next provider. Missing Vault secret → skip that
provider entirely (never crash). If all providers fail, raise `LLMUnavailable`
(typed exception, not a crash). The router protocol (`LLMRouter`) does not
change — callers stay unaware of the fallback logic. Grok: `api.x.ai/v1`,
OpenAI-compatible SDK (add `openai` as a dep only if no other client fits;
document the choice). Claude: `anthropic` SDK already forbidden as a direct
import — route through the router abstraction. Log every fallback:
`{tenant_id, from, to, reason, latency_ms}`.

### AD-7.2 — Checkpoints: AsyncPostgresSaver, tenant-prefixed thread IDs

Add `langgraph-checkpoint-postgres` to `pyproject.toml`. The checkpointer is
created once in lifespan via `AsyncPostgresSaver.from_conn_string(db_url)`;
the tables are created via `await checkpointer.setup()`. Thread ID =
`f"{tenant_id}:{session_id}"` where `session_id` is a UUID per conversation
(generated at webhook entry, carried in the request). This ensures checkpoints
are tenant-scoped at the storage key level — The Wall holds even in LangGraph
state. Never share a thread_id across tenants.

### AD-7.3 — Supervisor topology: StateGraph with routing node

The supervisor is a `StateGraph` (not `create_react_agent` at the top level)
with:
- One `route` node: Gemini Flash classifies intent → one of
  `{order, inventory, finance, customer, advisor}`. If the intent is
  ambiguous, route to `advisor`.
- Five conditional edges to specialist sub-graphs (the existing agent
  StateGraphs are compiled sub-graphs, not re-implemented).
- One `respond` node: formats the sub-graph output into a final Lebanese Arabic
  reply, logs cost + routing decision.
- The checkpointer is attached at compile time: `supervisor.compile(checkpointer=checkpointer)`.
- Regression tests for routing cover ≥20 Lebanese Arabic queries with known
  expected targets (Task 7.10).

### AD-7.4 — FinanceAgent scope: read-only, Phase 7

`get_revenue_summary(tenant_id, days)` → reads `daily_revenue` via repo →
returns `{date: revenue_lbp}`. `flag_anomalies(tenant_id, revenue_history)` →
calls `app.state.anomaly_detector.flag_days(...)` directly (no HTTP call to
`/predictions/anomaly`). `explain_anomaly(anomaly_result)` → LLM call (Tier 1)
that generates a Lebanese Arabic explanation of the ML flag. No writes. The
agent has NO access to `draft_purchase_order` or `queue_reengagement`.

### AD-7.5 — CustomerAgent HIL: queue to approvals inbox, action key format

Action key: `"send_reengagement:{tenant_id}:{customer_id}"`. The `queue_reengagement`
tool calls `ActionGate.issue_token(action=action_key, tenant_id=...)` and writes
the draft + token to `pending_actions` (the Phase 4 approvals table). The tool
returns `{customer_id, draft_message, approval_token, approval_url}` to the
agent. The agent's reply to the owner says "drafted a message for customer X,
awaiting your approval in the dashboard." Owner approves → webhook fires (same
supplier-webhook path Phase 4 uses, action key disambiguates). **No actual
WhatsApp send in Phase 7** — the send side is `TODO: Phase 10`.

### AD-7.6 — AdvisorAgent: DI from app.state, no HTTP

The Advisor's tools receive predictors via `ToolContext` (same pattern as
`InventoryAgent.forecast_demand` in Phase 6). `ToolContext` gains
`churn_predictor` and `anomaly_detector` fields alongside the existing
`demand_predictor`. All three predictors come from `app.state` (loaded once
in lifespan). No HTTP calls from within the agent. The Advisor calls the
Python protocols directly → gets numbers → LLM explains (Constitution IV).

### AD-7.7 — Cost tracking: per-run DB row + LangSmith callback

New table `agent_runs`: `(id UUID PK, tenant_id, agent_name VARCHAR, model_name
VARCHAR, prompt_tokens INT, completion_tokens INT, cost_usd NUMERIC(10,6),
created_at)`. A LangSmith/LangChain callback (`CostTrackingCallback`) is
attached to each agent; on `on_llm_end` it reads `response.llm_output.token_usage`
and writes the row (async, non-blocking). Model pricing constants live in
`agents/llm/pricing.py` (Flash/Pro/Grok/Haiku). Admin endpoint `GET
/admin/costs?from_date=&to_date=` (founder role only) aggregates by tenant +
agent + day. The Wall: `tenant_id` on every row; admin endpoint can filter by
tenant but cannot return all-tenants data without the admin role check.

### AD-7.8 — Tool allowlists: two-layer enforcement

Layer 1 (routing): The supervisor only routes owner messages to all 5 agents;
a customer message from the webhook STILL goes straight to `OrderAgent` via
the existing `MessageDispatcher` customer path (unchanged from Phase 2). The
supervisor is owner-only.

Layer 2 (per-agent): Each specialist agent's `StateGraph` only has edges to
its own tools. `FinanceAgent` has no edge to `draft_purchase_order`.
`CustomerAgent` has no edge to `check_stock`. This is structural — the graph
topology is the allowlist, not a string check. A test for each agent tries to
inject a forbidden tool call and verifies the graph has no path to it.

---

## New data model (Task 7.2 + 7.9)

Two new migrations in Phase 7:

```
agent_runs          — cost / usage per LLM call inside an agent run
  id              UUID PK default gen_random_uuid()
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
  agent_name      VARCHAR(64) NOT NULL        -- "finance", "customer", etc.
  model_name      VARCHAR(64) NOT NULL        -- "gemini-flash-1.5", etc.
  prompt_tokens   INTEGER NOT NULL DEFAULT 0
  completion_tokens INTEGER NOT NULL DEFAULT 0
  cost_usd        NUMERIC(10,6) NOT NULL DEFAULT 0
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

  INDEX: (tenant_id, created_at)   ← The Wall on reads; time filter on aggregates
```

LangGraph checkpoint tables are managed by `AsyncPostgresSaver.setup()` —
they are NOT in our Alembic migrations (the library owns its schema). Document
this distinction in DECISIONS.md.

---

## Phase 7 shape (end-to-end architecture)

```
WhatsApp webhook
  │  (Phase 1 identity resolver)
  ├─ role == "customer" ──► OrderAgent (unchanged Phase 2 path)
  │
  └─ role == "owner" ──► Supervisor StateGraph (NEW)
                              │  thread_id = f"{tenant_id}:{session_id}"
                              │  checkpointer = AsyncPostgresSaver (Postgres)
                              │
                        ┌─────▼──────────────────────────────────────────────┐
                        │  route node                                         │
                        │  GeminiFlash classifies intent                      │
                        │  → order | inventory | finance | customer | advisor │
                        └──────────────────┬─────────────────────────────────┘
                                           │
               ┌───────────────────────────┼───────────────────────────┐
               ▼                           ▼                           ▼
         OrderAgent              InventoryAgent               FinanceAgent
         (Phase 2)               (Phase 4)                    (Phase 7 NEW)
         [owner variant]         [unchanged]                  get_revenue_summary
                                                              flag_anomalies → AnomalyDetector
                                                              explain_anomaly → LLM (Tier 1)

               ▼                                                        ▼
         CustomerAgent                                          AdvisorAgent
         (Phase 7 NEW)                                          (Phase 7 NEW)
         get_churn_risks → ChurnPredictor                       get_todays_snapshot
         draft_reengagement → LLM                              get_demand_forecast → DemandPredictor
         queue_reengagement → ActionGate (HIL L2)              get_churn_summary → ChurnPredictor
                                                               get_anomaly_status → AnomalyDetector
                                                               compose_briefing → LLM (Tier 2)

                              ▲ all predictors injected from app.state via ToolContext
                              ▲ all DB reads go through tenant-scoped repositories
```

---

## Phase 7 — Tasks Overview

| # | Task | Branch |
|---|------|--------|
| 7.1 | LLM Router fallback chain (Gemini Flash → Grok → Claude Haiku) | `feature/MOD-7-llm-router` |
| 7.2 | Postgres-backed checkpointer (AsyncPostgresSaver, tenant-prefixed thread IDs) | `feature/MOD-7-checkpointer` |
| 7.3 | FinanceAgent (revenue summary + anomaly explanation, read-only) | `feature/MOD-7-finance-agent` |
| 7.4 | CustomerAgent (churn risks + re-engagement draft, HIL L2) | `feature/MOD-7-customer-agent` |
| 7.5 | AdvisorAgent (morning briefing, all ML tools, LLM synthesis) | `feature/MOD-7-advisor-agent` |
| 7.6 | Supervisor topology (StateGraph, routing node, sub-graph wiring, checkpoint attach) | `feature/MOD-7-supervisor` |
| 7.7 | Owner dispatch wiring (replace OWNER_PLACEHOLDER with supervisor, end-to-end test) | `feature/MOD-7-owner-dispatch` |
| 7.8 | Tool allowlists (structural enforcement + tests) | `feature/MOD-7-tool-allowlists` |
| 7.9 | Cost tracking (agent_runs table + migration + callback + admin API) | `feature/MOD-7-cost-tracking` |
| 7.10 | Routing golden evals + CI gate (≥20 Arabic queries, accuracy ≥ 0.85) | `feature/MOD-7-routing-evals` |
| 7.11 | Checkpoint resume test (simulate restart mid-run, verify state recovery) | `feature/MOD-7-checkpoint-test` |
| 7.12 | Guardrails consolidation (extend to all 5 agents per ROADMAP insertion) | `feature/MOD-7-guardrails` |
| 7.13 | Frontend: owner chat panel (RTL, Arabic, shows agent name on response) | `feature/MOD-7-owner-chat-ui` |
| 7.14 | CI guards + DECISIONS.md (routing accuracy pinned, Phase 7 defend-it Q&A) | `feature/MOD-7-ci-guards` |

14 tasks. One branch per task, one PR per task, approval before each commit.

---

## Task 7.1 — LLM Router fallback chain

Replace `GeminiRouter` with a `FallbackLLMRouter` that tries providers in order:
`Gemini Flash → Grok → Claude Haiku`. The `LLMRouter` protocol (`tier1()`,
`tier2()`) does NOT change — all callers stay untouched.

**What ships:**
- `agents/llm/router.py`: `FallbackLLMRouter(providers: list[LLMRouter])` —
  tries each in turn; catches `RateLimitError`, `TimeoutError`, `APIError`; logs
  every activation with `{tenant_id, from, to, reason, latency_ms}`; raises
  `LLMUnavailable` if all fail.
- `agents/llm/grok_router.py`: thin wrapper around the Grok API
  (OpenAI-compatible, model `grok-3-mini` for Tier 1, `grok-3` for Tier 2).
  Key from Vault (`grok_api_key`). Missing key → skip this provider, log a
  warning at startup.
- `agents/llm/anthropic_router.py`: thin wrapper for Claude Haiku / Sonnet
  via Anthropic SDK (`anthropic` package). Key from Vault (`anthropic_api_key`).
  IMPORTANT: import `anthropic` only inside this file (provider-SDK import
  quarantine — same pattern as gemini is imported only inside `gemini_router`).
- `agents/llm/pricing.py`: pricing constants per model (used by Task 7.9).
- `Settings`: `grok_api_key`, `anthropic_api_key` resolved from Vault. Missing
  key is a WARNING (not fatal) — the system runs with fewer fallback options.
- `main.py` lifespan: builds `FallbackLLMRouter([GeminiRouter, GrokRouter,
  AnthropicRouter])`, skipping any provider whose key is absent.

**Tests:** mock primary to raise `RateLimitError` → assert Grok fires + log line
written. Mock both primary and Grok → assert Haiku fires. Mock all three →
assert `LLMUnavailable` raised (not a crash). Verify no provider-SDK import leaks
outside the provider-specific module (grep in test).

**DoD:** CI green (provider-SDK gate still passes); the fallback chain activates
and logs correctly on injected errors; the Settings class resolves or skips each
key gracefully.

---

## Task 7.2 — Postgres-backed checkpointer

Add `langgraph-checkpoint-postgres` to `pyproject.toml` (pinned). Wire
`AsyncPostgresSaver` in lifespan.

**What ships:**
- `pyproject.toml`: `langgraph-checkpoint-postgres>=2.0.0` (check latest stable).
- `app/main.py` lifespan: `checkpointer = await AsyncPostgresSaver.from_conn_string(db_url); await checkpointer.setup()`. Stored as `app.state.checkpointer`.
- `app/infra/checkpointer.py`: tiny helper `make_thread_id(tenant_id, session_id) -> str` returning `f"{tenant_id}:{session_id}"` — the single function that enforces the tenant prefix. Used everywhere a thread_id is formed; never inlined.
- Migration note (in a code comment + DECISIONS): LangGraph checkpoint tables are NOT in Alembic. `checkpointer.setup()` is idempotent and runs at startup. Document this.
- No new Alembic revision for this task.

**Tests:** integration test (requires real DB) — write a checkpoint under
`make_thread_id(tenant_a, session_1)`, assert it is readable under the same ID,
assert it is NOT readable under `make_thread_id(tenant_b, session_1)` (different
tenant, same session UUID). Mark with `@pytest.mark.integration` so it only runs
when `TEST_DB_URL` is set (offline CI skips it).

**DoD:** lifespan completes without error on a running DB; checkpoint tables
exist; the tenant-prefix helper is the only place thread IDs are formed; the
cross-tenant isolation integration test passes.

---

## Task 7.3 — FinanceAgent

New agent: `app/agents/finance/` — cash flow reading + anomaly explanation.
Read-only. Constitution IV: AnomalyDetector produces the flags; LLM only explains.

**What ships:**
- `agents/finance/agent.py`: `FinanceAgent(llm_router, anomaly_detector)` — a
  `StateGraph` with three tools in sequence or on demand from the owner's query.
- `agents/finance/tools.py`:
  - `get_revenue_summary(ctx, days: int = 30) -> RevenueSummary` — reads
    `TrainingDataRepository.daily_revenue(tenant_id, days)`, returns date →
    revenue_lbp dict + total + average + trend direction.
  - `flag_anomalies(ctx, revenue_history: dict) -> AnomalyResult` — calls
    `ctx.anomaly_detector.flag_days(tenant_id, revenue_history)`, returns
    flagged days with their revenues.
  - `explain_anomaly(ctx, anomaly_result: AnomalyResult) -> str` — Tier 1 LLM
    call that generates a Lebanese Arabic explanation: what happened on the
    flagged days, what's normal vs abnormal, what could the cause be.
- `agents/finance/schemas.py`: `RevenueSummary`, `AnomalyResult` Pydantic models.
- `prompts/finance_agent_ar.py`: system prompt (owner is asking about their
  finances, reply in Lebanese Arabic), anomaly explanation template.
- `ToolContext` gains `anomaly_detector: AnomalyDetector` field (alongside the
  existing `demand_predictor`).

**Tests:** unit tests — `flag_anomalies` with a stub detector returns the stub's
output; `explain_anomaly` with a mocked LLM verifies the prompt template is used;
cross-tenant: inject two `tenant_id` values, assert each agent run returns only
that tenant's revenue data.

**DoD:** FinanceAgent can be instantiated standalone (not in supervisor yet);
`explain_anomaly` uses ML output, not an LLM that "guesses" the anomaly; tenant
isolation test passes; `uv run pytest` green.

---

## Task 7.4 — CustomerAgent

New agent: `app/agents/customer/` — churn risk surface + re-engagement draft,
HIL Level 2 for send.

**What ships:**
- `agents/customer/agent.py`: `CustomerAgent(llm_router, churn_predictor, action_gate)`.
- `agents/customer/tools.py`:
  - `get_churn_risks(ctx, top_n: int = 10) -> ChurnRisks` — calls
    `ctx.churn_predictor.predict_risks(tenant_id, orders, as_of=today)`,
    fetches orders via repo, returns top-N at-risk customers with names +
    probabilities. Sorted descending by risk.
  - `draft_reengagement(ctx, customer: CustomerRisk) -> DraftMessage` — Tier 1
    LLM call generating a warm Lebanese Arabic re-engagement WhatsApp message
    personalised to the customer (uses their name, their last product, recency).
  - `queue_reengagement(ctx, customer_id: str, draft: str) -> QueuedAction` —
    calls `ActionGate.issue_token(action=f"send_reengagement:{tenant_id}:{customer_id}", ...)`,
    writes to pending approvals, returns `{approval_token, approval_url}`. Does
    NOT send. Level 2 HIL — human must approve.
- `agents/customer/schemas.py`: `CustomerRisk`, `ChurnRisks`, `DraftMessage`,
  `QueuedAction`.
- `prompts/customer_agent_ar.py`: system prompt + re-engagement message template
  (warm Lebanese Arabic, addresses customer by name, references their last order).
- `ToolContext` gains `churn_predictor: ChurnPredictor` field.

**Tests:** `get_churn_risks` with a stub predictor returns deterministic scores;
`queue_reengagement` writes to approvals inbox and does NOT trigger any send;
cross-tenant: two tenants' at-risk customers never appear in each other's results.

**DoD:** CustomerAgent can be instantiated standalone; `queue_reengagement`
produces an approval token (verified by `ActionGate.verify_token(...)`); the
agent has no tool path to actually send a message; tenant isolation test passes.

---

## Task 7.5 — AdvisorAgent

New agent: `app/agents/advisor/` — strategic synthesis + morning briefing.
Calls all three Phase 6 predictors + Phase 5 RAG. The richest "ML predicts,
LLM explains" showcase in the system.

**What ships:**
- `agents/advisor/agent.py`: `AdvisorAgent(llm_router, demand_predictor, churn_predictor, anomaly_detector, kb_service)`.
- `agents/advisor/tools.py`:
  - `get_todays_snapshot(ctx) -> TodaySnapshot` — today's revenue vs 7-day avg
    vs same-day last week; top 3 products by revenue; order count. Pure DB reads.
  - `get_demand_forecast(ctx) -> DemandForecast` — calls
    `ctx.demand_predictor.predict_quantity(...)` for each low-stock product
    (those below threshold), returns a list of `{product, predicted_units_7d,
    current_stock}`.
  - `get_churn_summary(ctx) -> ChurnSummary` — calls
    `ctx.churn_predictor.predict_risks(...)`, returns count at risk + top-3 names.
  - `get_anomaly_status(ctx) -> AnomalyStatus` — calls
    `ctx.anomaly_detector.flag_days(...)`, returns `{is_today_anomalous, recent_flags}`.
  - `compose_briefing(ctx, snapshot, demand, churn, anomaly) -> str` — Tier 2
    LLM call (Gemini Pro) synthesising all four inputs into a concise Lebanese
    Arabic morning briefing: "كيف رح تكون يومك" format, actionable items first.
- `agents/advisor/schemas.py`: all input/output Pydantic models.
- `prompts/advisor_agent_ar.py`: system prompt + briefing composition template
  (structured: revenue status, demand outlook, at-risk customers, anomaly alert).

**Tests:** all four ML tool wrappers tested with stubs; `compose_briefing` tested
with a mocked LLM verifying it receives the structured inputs (not raw numbers
passed to the LLM to "figure out"); cross-tenant test.

**DoD:** `python -c "from app.agents.advisor.agent import AdvisorAgent"` works;
all ML tools use the predictor protocols (no HTTP); LLM only receives structured
data to explain (not raw data to analyze); tenant isolation passes.

---

## Task 7.6 — Supervisor topology

The main deliverable of Phase 7: `app/agents/supervisor/agent.py`, a LangGraph
`StateGraph` that routes owner messages to the right specialist sub-graph and
persists checkpoints.

**What ships:**
- `agents/supervisor/agent.py`: `OwnerSupervisor(llm_router, order_agent, inventory_agent, finance_agent, customer_agent, advisor_agent, checkpointer)`. Compiled once in lifespan.
- `agents/supervisor/routing.py`: `classify_intent(message: str, llm) -> Intent`
  where `Intent = Literal["order", "inventory", "finance", "customer", "advisor"]`.
  Uses Tier 1 LLM with a structured output / function call. On parsing failure,
  defaults to `"advisor"` (never crashes). Fully tested standalone.
- `agents/supervisor/state.py`: `SupervisorState(TypedDict)` with `messages`,
  `tenant_id`, `session_id`, `intent`, `response`, `routed_to` fields.
- `prompts/supervisor_ar.py`: intent-classification system prompt listing what
  each agent handles (in Arabic terms the owner would use, not internal names),
  plus the final-response format instructions.
- The supervisor StateGraph:
  - Node `route`: classifies intent, sets `state.intent + state.routed_to`.
  - Conditional edges → one of five compiled sub-graphs.
  - Node `respond`: packages the sub-graph output, logs the routing decision +
    cost record (if CostTrackingCallback is wired — Task 7.9).
  - Attached to `app.state.checkpointer` at compile time.
- `main.py` lifespan: builds `OwnerSupervisor` from `app.state.*` and stores
  as `app.state.supervisor`.

**Tests:**
- `test_supervisor_routing.py`: for each of the 5 intent classes, inject a mock
  LLM that returns that class, verify the right sub-graph is invoked.
- Ambiguity test: LLM returns an unparseable string → falls back to `"advisor"`.
- Checkpoint test (basic): run supervisor one step, call with same thread_id,
  verify state carries over (full resume tested in Task 7.11).

**DoD:** supervisor compiles; routing test covers all 5 agents + fallback; it
attaches to the checkpointer; `uv run pytest` green.

---

## Task 7.7 — Owner dispatch wiring

Replace `OWNER_PLACEHOLDER` in `services/dispatcher.py` with the real supervisor.
End-to-end: owner WhatsApp message → identity resolved → supervisor → specialist
agent → Lebanese Arabic reply.

**What ships:**
- `services/dispatcher.py`: owner branch now calls
  `await supervisor.handle(message, tenant_id, session_id)` instead of returning
  the placeholder string.
- `app/infra/session.py` (new or inline): `make_session_id(identity) -> str` —
  derives or generates a session UUID per conversation. For now: one session per
  webhook call unless a checkpoint with the same thread_id already exists
  (detected by checking `checkpointer.aget_tuple(thread_id)`).
- `api/webhooks.py`: thread the session_id through to dispatcher.
- `main.py`: `dispatcher = MessageDispatcher(order_agent, supervisor, sessionmaker)`.
  Constructor gains a `supervisor` parameter; existing 3-arg call sites for
  tests are updated (or a default is provided).

**Tests:**
- `test_dispatcher_owner.py`: inject a mock supervisor, send an owner-role
  webhook, assert the supervisor's handle method was called with the right
  `tenant_id`; assert the response is returned to the caller.
- `test_dispatcher_customer.py`: existing customer path tests unchanged and green.
- Integration smoke test (can be manual / marked `@pytest.mark.integration`):
  an owner message through the full stack returns a non-empty Arabic response.

**DoD:** `uv run pytest` green; the OWNER_PLACEHOLDER is gone; both owner and
customer paths work; the dispatcher test covers both roles.

---

## Task 7.8 — Tool allowlists

Structural enforcement of per-agent tool isolation. The graph topology IS the
allowlist (AD-7.8). Add tests that PROVE the graph has no path to forbidden tools.

**What ships:**
- `tests/test_tool_allowlists.py`: for each agent, enumerate the compiled graph's
  nodes and edges. Assert:
  - FinanceAgent has no node named `draft_purchase_order`, `queue_reengagement`, or
    any tool not in its own `agents/finance/tools.py`.
  - CustomerAgent has no node named `draft_purchase_order`, `check_stock`, or any
    inventory/finance tool.
  - AdvisorAgent has no node for any HIL action tool.
  - The supervisor routes owner messages only; the customer path in `dispatcher.py`
    never calls the supervisor.
- `agents/supervisor/routing.py`: `classify_intent` is called ONLY for owner
  messages. The dispatcher is the first gate (role check), the routing node is
  the second gate (intent). Document both layers in a comment.
- No new code changes to the agents themselves (tools are already separate by
  module). This task is tests + documentation + any fixes found.

**DoD:** all allowlist tests pass; no new `print(` or raw SQL; `uv run pytest` green.

---

## Task 7.9 — Cost tracking

New table, migration, LangSmith callback, and admin API endpoint.

**What ships:**
- `db/models/agent_runs.py`: `AgentRun` SQLAlchemy model (schema above in the
  data model section).
- Alembic migration: `add_agent_runs_table`.
- `agents/llm/pricing.py`: `COST_PER_1K_TOKENS: dict[str, tuple[float, float]]`
  (input, output costs per model name). Sources: documented model pricing pages;
  mark as "approximate" in a comment.
- `agents/llm/cost_callback.py`: `CostTrackingCallback(async_session_factory,
  tenant_id, agent_name)` — a LangChain `AsyncCallbackHandler` that on
  `on_llm_end` reads `token_usage`, computes cost, writes `AgentRun` row async.
  Never raises (log errors, continue — a failed cost row is not a failed run).
- `repositories/agent_runs.py`: `AgentRunRepository(tenant_id, session)` — Wall-
  enforced. Methods: `create(...)`, `daily_summary(from_date, to_date)`.
- `api/admin.py` (extend): `GET /admin/costs?tenant_id=&from_date=&to_date=`
  returns `{date: {agent: cost_usd}}`. Requires `role == "admin"` (founder role).
- Wire the callback into the supervisor's compiled graph (pass to `config` in
  `handle()` or attach at compile time).

**Tests:** inject a mock LLM that returns token usage; assert an `AgentRun` row
is written with correct tenant_id; assert the cross-tenant query returns only
one tenant's rows; assert a missing `token_usage` (older LangChain behavior)
doesn't crash the callback.

**DoD:** migration runs cleanly; cost row written on every agent run; admin
endpoint returns per-day aggregates; `uv run pytest` green.

---

## Task 7.10 — Routing golden evals + CI gate

Routing accuracy is code; test it like code. ≥20 Lebanese Arabic queries with
known expected agents, a threshold gate, and a CI step.

**What ships:**
- `app/agents/eval/routing_golden.json`: ≥20 entries, each
  `{query_ar, expected_agent, notes}`. Mix of clear-cut and slightly ambiguous
  queries. At least 4 per agent class, plus 2 intentionally ambiguous ones
  (expected: `"advisor"`).
- `app/agents/eval/evaluate_routing.py`: loads the golden set, runs
  `classify_intent(query, stub_llm)` for each — where `stub_llm` is a
  pre-programmed mock OR the real Gemini (controlled by `ROUTING_EVAL_REAL_LLM=1`
  env var). Computes accuracy per class + overall. Exits 1 if accuracy < threshold.
- `app/agents/eval/routing_thresholds.yaml`: `overall_accuracy_min: 0.85`,
  `per_class_min: 0.70`.
- CI (`ci.yml`): a new step `routing-eval` that runs `python -m
  app.agents.eval evaluate_routing` with the mock LLM (offline, deterministic,
  fast). The real-LLM variant is documented but not run in CI (costs money).
- `tests/test_routing_golden.py`: proves the eval script exits 1 when a mock
  router mis-classifies more than the threshold allows.

**DoD:** 20+ golden queries committed; CI step green; the "break it" test
(reduce threshold to 1.0 in a test fixture) fails CI; `uv run pytest` green.

---

## Task 7.11 — Checkpoint resume test

Explicit test proving the ROADMAP DoD: "Kill the agent container mid-run.
Restart it. It resumes from the last checkpoint."

**What ships:**
- `tests/test_checkpoint_resume.py`:
  1. Build a `OwnerSupervisor` with the real `AsyncPostgresSaver` (integration,
     requires DB; `@pytest.mark.integration`).
  2. Run the supervisor through the `route` node only (inject a mock intent
     classifier that produces a known intent but the sub-graph raises a
     `SimulateCrash` exception before completing).
  3. Verify a checkpoint was written (call `checkpointer.aget_tuple(thread_id)`
     and assert the `route` step's state is present).
  4. Create a NEW `OwnerSupervisor` instance using the SAME checkpointer.
  5. Resume with the same `thread_id`. Assert the sub-graph starts from the
     saved state, not from the beginning.
  6. Assert `routed_to` in the resumed state matches the original routing
     decision (no re-routing on resume).
- A `SimulateCrash` exception class in `tests/` only (never in `app/`).

**DoD:** integration test passes on a running DB; the test proves state survives
a "restart" (new instance, same checkpointer + thread_id); CI skips it when
`TEST_DB_URL` is absent; `uv run pytest -m "not integration"` still green.

---

## Task 7.12 — Guardrails consolidation

Per ROADMAP "Planned Insertions": "Consolidated across agents in Phase 7."
The existing `agents/guardrails.py` has input/output rails for the OrderAgent.
Extend them to all five agents consistently.

**What ships:**
- `agents/guardrails.py` extensions:
  - `check_input(text, role)` — already exists; add a `max_length` check (≥ 4000
    chars → truncate + warn); extend injection patterns to Arabic transliterations
    ("system:", "ignore previous", Arabic equivalents).
  - `check_output(text, agent_name, catalog_items=None)` — extend "no hallucinated
    catalog item" rule to FinanceAgent output (no invented product names), to
    AdvisorAgent briefing (no invented revenue numbers), to CustomerAgent
    re-engagement draft (no invented customer history).
  - `redact_pii(text)` — already exists; add Lebanese phone patterns
    (`+961 X XXXXXX`, `0X-XXXXXX`).
- Each of the three new agents wraps its LLM call output through `check_output`
  before returning. OrderAgent and InventoryAgent already do this; verify and
  fix if not.
- `tests/test_guardrails.py` (extend): inject a prompt-injection string in
  Arabic → assert blocked; inject an output with a fabricated product name for
  the finance context → assert flagged; inject a Lebanese phone number → assert
  redacted.

**DoD:** all 5 agents pass output through guardrails; injection test covers Arabic
patterns; hallucination check covers FinanceAgent + AdvisorAgent output; `uv run
pytest` green.

---

## Task 7.13 — Frontend: owner chat panel

Replace the "owner chat coming soon" placeholder with a working chat panel.
Minimal but functional: text input, conversation thread, response shows which
agent handled it.

**What ships:**
- `frontend/src/pages/OwnerChatPage.tsx`: the main chat view. RTL, Arabic labels.
  Text input at bottom (Lebanese Arabic placeholder: "اكتب سؤالك هون...").
  Messages thread above (owner bubbles right, Modir bubbles left, agent name
  badge on each Modir bubble e.g. "المستشار" / "المالية" / "المخزون").
- `frontend/src/api/chat.ts`: `sendOwnerMessage(text: string): Promise<ChatResponse>`.
  Posts to the webhook endpoint (or a new `POST /chat` endpoint if the webhook
  format is unsuitable for browser use — decide and document).
- If a new `/chat` endpoint is cleaner (avoids WhatsApp webhook format in the
  browser): `api/chat.py` (new router), `POST /chat` accepting `{message: str}`,
  returning `{response: str, agent: str, session_id: str}`. Auth: `get_current_user`.
- Nav item in the sidebar: "المحادثة" (Chat) with a chat bubble icon.
- i18n strings for chat labels added.
- `npm run lint && npm run typecheck && npm run build` green.
- Functional, not polished ([[phase3-frontend-polish-pending]]).

**DoD:** owner can type a question in the dashboard and receive a Modir reply
showing which agent answered; RTL correct; mobile-first (360px); build green.

---

## Task 7.14 — CI guards + DECISIONS

The final task. CI guard that pins Phase 7 invariants; local docs (gitignored).

**What ships:**
- `tests/test_phase7_ci_guards.py`:
  - Supervisor loads from `app.state` when lifespan runs (mock lifespan).
  - `ml_mode` defaults to `"stub"` (Phase 6 guard still holds).
  - The fallback router's provider list is non-empty.
  - `make_thread_id` always produces a string prefixed with tenant_id
    (property-based: for any UUID pair, the result starts with `str(tenant_id)`).
  - The routing golden-eval script exits 1 when a mock router produces all-wrong
    outputs (regression guard).
- `docs/DECISIONS.md` (local, gitignored): append Phase 7 section covering —
  router fallback chain design (why ordered; what each provider covers; cost
  tier), supervisor topology (StateGraph vs create_react_agent trade-offs),
  checkpoint thread ID design (why tenant prefix; why not session only), HIL
  re-engagement (why queue and not send; Phase 10 forward reference), cost
  tracking approach (callback vs middleware), tool allowlist layers (why
  structural not string-based).
- `docs/PHASE_7_DEFEND_IT.md` (local, gitignored): all defend-it Q&A answered.
- Commit = `tests/test_phase7_ci_guards.py` only (docs/ gitignored).

**DoD:** CI green on all guards; the phase 7 defend-it questions are answerable
without notes; `uv run pytest` green.

---

## Phase 7 — Definition of Done

- [ ] `app.state.supervisor` is built in lifespan (all five sub-graphs wired).
- [ ] An owner message "كيف مبيعاتي اليوم؟" routes to AdvisorAgent → calls
  AnomalyDetector + revenue repo → replies in Lebanese Arabic. The LLM does not
  produce the anomaly flag — it only explains it.
- [ ] Kill the process mid-run (or simulate via test). Create a new instance with
  the same thread_id. Verify it resumes from the last checkpoint without re-routing.
- [ ] Force Gemini to raise `RateLimitError`. The router falls through to Grok.
  A structured log line records the fallback with `{from, to, reason}`.
- [ ] An attempt to call a tool not in an agent's graph produces no path (verified
  structurally in `test_tool_allowlists.py`).
- [ ] An `agent_runs` row is written for every LLM call. `GET /admin/costs` returns
  per-day totals for a tenant.
- [ ] Routing golden evals: ≥20 Lebanese Arabic queries, ≥0.85 overall accuracy on
  the offline mock-LLM eval. CI step is green.
- [ ] All five agents' outputs pass through `check_output` guardrails. An injected
  Arabic prompt-injection string is blocked.
- [ ] Owner types a question in the dashboard chat panel and receives an Arabic reply
  with the responding agent's name shown.
- [ ] `uv run pytest -m "not integration"` green (all suite). Integration tests green
  with `docker compose up -d db`. CI green on push.
- [ ] The Wall holds in Phase 7: every new tool reads through tenant-scoped repos;
  checkpoint thread IDs are tenant-prefixed; `agent_runs` is tenant-scoped.

---

## Phase 7 — Defend-it preparation

- Show me your supervisor's routing logic. What happens when the owner asks
  something ambiguous like "فيك تساعدني؟" (can you help me)?
- The AdvisorAgent calls three Phase 6 predictors. Show me where the numbers
  come from — and where the LLM is and is not in the path.
- How do you guarantee two retries of "queue re-engagement for customer X"
  don't queue two approval requests? (Idempotency key on the ActionGate.)
- Show me the checkpoint thread ID for Tenant A, session S. What happens if
  Tenant B uses the same session UUID?
- The Grok API key is not in Vault. What happens at startup? At runtime?
- Walk me through a cost row: which callback writes it, when, what token counts
  are captured, and how the cost is estimated.
- An LLM inside the FinanceAgent returns a response that includes a product name
  that doesn't exist in the catalog. What happens?
- What does "tool allowlist enforced structurally" mean? Show me in the graph.
- The owner sends a 5,000-character message. What happens?
- How would you add a sixth specialist agent in Phase 8?

---

## Ready for Phase 8?

You are ready when:
- Every checkbox above is checked.
- `uv run pytest -m "not integration"` is green; integration tests green with DB up.
- An end-to-end demo shows: owner types "كيف مبيعاتي اليوم؟" → supervisor routes
  to Advisor → pulls live revenue + ML predictions → replies in Lebanese Arabic
  with an explanation (not the raw numbers).
- The checkpoint resume test passes (kill + restart + resume from state).
- The fallback router test shows Grok firing when Gemini is mocked to fail.
- Cost rows exist in `agent_runs` for each agent call in the demo.

Phase 8 is Hardening and Production Readiness: load tests (100 concurrent
customers across 10 tenants), chaos tests (kill each container), red-team evals
(prompt injection in CI per the ROADMAP guardrails insertion), structured logging
to a real aggregator, and backup/restore. Do not start it until every Phase 7
DoD checkbox is checked and the defend-it questions are answerable out loud.
