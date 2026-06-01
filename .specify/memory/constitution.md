# Modir Constitution

> The non-negotiable engineering rules for Modir. Every phase reads this file
> FIRST. When the ROADMAP or a phase file conflicts with this constitution,
> the constitution wins. You must be able to defend every line of code you
> write against these principles.

## Core Principles

### I. The Wall — Tenant Isolation Is Sacred (NON-NEGOTIABLE)

Modir is multi-tenant SaaS. Many businesses share one platform; their data
must NEVER leak across the wall.

- Every database row carries a non-nullable, indexed `tenant_id`.
- Every repository method takes `tenant_id` as a required parameter. No method
  queries without it. There is one repository base class and it enforces this.
- The service layer never bypasses the repository to hit raw SQL "just once."
- `tenant_id` in a JWT is a claim, not a trust anchor: the token says which
  tenant, but the database query still filters by it independently.
- JOINs are scoped on both sides — Tenant A's order joining Tenant B's product
  is a leak and is treated as a Sev-1 bug.
- Vector search filters by `tenant_id` before similarity, never after.
- Isolation is enforced in code, never in prompts. A failing test that *tries*
  to cross the wall and is blocked must exist.

### II. Secrets Live in Vault, Never in Code or Env (NON-NEGOTIABLE)

- All secrets (LLM keys, MinIO keys, JWT signing secret) resolve from
  HashiCorp Vault at startup. `.env` holds only non-secret config plus the
  Vault address and token.
- There is exactly one `Settings` class (pydantic-settings). Every config value
  is typed and validated. The app refuses to start if a required value is
  missing or a secret cannot be resolved.
- `os.getenv()` appears nowhere outside the `Settings` class. `grep -rn
  "os.getenv" backend/app/` returns nothing. CI fails the build if it does.
- `grep -ri "api_key" backend/app/` returns zero matches outside `vault.py` and
  `settings.py`.

### III. Observability From the First Commit (NON-NEGOTIABLE)

- Structured logging only. `structlog` configured for JSON output to file AND
  stdout. `print()` appears nowhere in `backend/app/`. CI fails on `print(`.
- Every log line that touches a tenant carries `tenant_id`. Agent/LLM work
  carries the trace context (LangSmith) from the phase it is introduced.
- Sensitive data is redacted before any log line leaves the service boundary:
  phone numbers, names, API keys, card numbers, IDs. The redaction layer is
  tested in CI — inject a fake key, assert it never appears unredacted.
- Observability is core infrastructure, not an afterthought bolted on at the
  end.

### IV. ML Predicts, LLM Explains — Right Tool for Each Job

- Numerical/statistical problems (forecasting, churn, anomaly, segmentation)
  use real trained ML models (scikit-learn / XGBoost / LightGBM / Prophet),
  not LLM prompts.
- LLMs are reserved for language: understanding Lebanese Arabic, generating
  briefings, explaining ML output, structuring OCR text.
- ML pipelines prevent leakage (preprocessing inside `sklearn.Pipeline`),
  use cross-validation, report per-class metrics on imbalanced problems, and
  log every experiment to `results.csv`. Models train in version-controlled
  code, never only in a notebook.
- Models load once via the FastAPI `lifespan` handler and are served through
  dependency injection — never loaded inside a route handler.

### V. Human in the Loop Is Enforced Architecturally (NON-NEGOTIABLE)

- Actions are tagged with an autonomy level: Level 1 (full auto, low-risk,
  reversible), Level 2 (human approves before execute), Level 3 (human
  initiates only).
- There is a single execution gate. Any action requesting execution carries
  either an "auto" tag (Level 1) or a signed approval token. No code path
  bypasses the gate — even an accidental call without authorization is rejected.
- The AI prepares; the human approves; the system executes. Wrong actions have
  real-world consequences (money, suppliers, customers), so HIL is not optional
  on Level 2+ actions.

## Architecture & Technology Constraints

- **Layered structure, enforced:** `api/` -> `services/` -> `repositories/` ->
  `db/`, with `domain/`, `agents/`, `ml/`, `ocr/`, `infra/` as cross-cutting.
  Dependencies point inward; a route never imports the ORM directly, a
  repository never imports a router.
- **Stack (fixed):** Python 3.11, FastAPI, SQLAlchemy 2.x async, Alembic,
  structlog, Postgres 16 + pgvector, Redis 7, MinIO, HashiCorp Vault (dev mode
  locally), LangGraph, React 18 + Vite (Phase 3+). Package manager is `uv` --
  `pip` is never invoked. `import requests` is banned; use `httpx.AsyncClient`.
  CI fails on `import requests`.
- **Containers first:** Development happens against `docker compose up`, not a
  bare-metal notebook. Services talk by name on the Docker network (`db:5432`,
  not `localhost:5432`). Migrations run in a dedicated `migrate` service that
  exits before `api` starts.
- **Provider-agnostic LLM:** Application code never imports a provider SDK
  directly. All LLM calls go through the LLM Router (Gemini primary -> Grok
  fallback -> Claude emergency). Adding a provider is a config change, not a
  code change.
- **Async correctness:** Never call a blocking SDK synchronously inside an
  async route. Tool inputs from an LLM are validated with Pydantic; bad output
  triggers a retry, not a crash. Slow side-effecting tools carry idempotency
  keys.

## Language, Workflow & Quality Gates

- **Language:** All user-facing text (to owners and customers) is in Lebanese
  Arabic dialect. Code, comments, variable names, and logs are in English.
  Prompts live in `prompts/` files, never as inline string literals.
- **Git workflow:** One task = one branch = one PR. Branch names follow the
  phase convention (e.g. `feature/MOD-0-settings`). Pause for human approval
  after each task before committing. Never let more than one task land
  unreviewed -- you cannot defend what you did not read.
- **Evaluation is the grade:** Accuracy claims are backed by a number on a real
  test set. A golden dataset is the ground truth. CI runs evals; thresholds in
  `eval_thresholds.yaml` block merges on regression. LLM-as-judge output is
  spot-checked against human labels.
- **Definition of done is binding:** Do not advance to the next phase until the
  current phase's Definition of Done is fully checked and its defend-it
  questions can be answered fluently, out loud, without notes.

## Governance

This constitution supersedes all other practices. The ROADMAP and phase files
operate within it; on conflict, this document wins.

- Every PR is reviewed against these principles. The "forbidden patterns" CI
  gate (`os.getenv`, `print(`, `import requests`, unredacted secrets) is a hard
  block, not a warning.
- Complexity must be justified. Prefer the simplest design that satisfies the
  Wall, HIL, and observability rules.
- Amendments require an explicit version bump below, a one-line rationale in
  the commit, and a note of which phases are affected.
- When in doubt, pause and ask. One wrong architectural decision compounds
  across every future phase.

**Version**: 1.0.0 | **Ratified**: 2026-06-01 | **Last Amended**: 2026-06-01
