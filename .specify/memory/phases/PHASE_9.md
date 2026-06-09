# Phase 9 — Polish, Demo, and Documentation

> Anyone can clone the repo, run one command, and see Modir working.
> The repository is portfolio-ready: licensed, documented for contributors,
> secure-disclosure policy in place, and a worked demo anyone can follow.

Read `.specify/memory/constitution.md` FIRST. Then `.specify/memory/ROADMAP.md`
(Phase 9 section). Then this file. Work task by task; pause for approval after
each task before committing.

---

## Goal

Bring the repository to public portfolio quality:

1. **Open-source scaffolding** — LICENSE, CONTRIBUTING.md, SECURITY.md
2. **For-reviewers guide** — every defend-it question answered in writing so a
   technical reviewer can evaluate the project without live Q&A
3. **Demo seed script** — one command to populate a real Lebanese bakery shop
   (products, customers, orders, daily cost records) so the full flow can be
   demonstrated immediately after `docker compose up`
4. **Demo script** — click-by-click guide covering the full 5-minute demo:
   customer orders in Lebanese Arabic → owner dashboard → ML demand forecast →
   re-engagement draft → owner approves → manual order fallback
5. **README final polish** — "For Reviewers" section, demo quickstart, badges

---

## Resist scope creep

- **No new backend features.** Phase 8 is complete. Phase 9 adds zero new
  API endpoints, zero new DB columns, zero new agents.
- **No new frontend pages.** A seed script result is visible through the
  existing dashboard. No new UI components.
- **No deployment.** Railway / Fly.io deployment is marked optional in the
  ROADMAP and is out of scope for Phase 9 unless explicitly requested.
- **No test changes.** Existing tests stay green. The seed script is a dev
  helper, not a test fixture — it does not live in `tests/`.

---

## Prerequisites

- **Phase 8 is complete and merged to main.** README.md, DECISIONS.md, and
  RUNBOOK.md already exist at repo root.
- **`uv run pytest -m "not integration and not load"` is green on main.**

---

## Architecture decisions (Phase 9)

### AD-9.1 — MIT License

Modir is a portfolio / educational project. MIT is the standard choice: simple,
permissive, compatible with all dependencies. Apache 2.0 was considered but
adds patent grant language that is unnecessary here. MIT wins on simplicity.

### AD-9.2 — For-reviewers guide in `docs/`

A dedicated `docs/FOR_REVIEWERS.md` keeps the README readable while giving
evaluators a single, deep Q&A document. Inline answers in the README would
make it too long. The file answers every defend-it question from Phases 0–8
with concrete code references (file + line) so reviewers can verify answers
directly in the codebase.

### AD-9.3 — Seed script as a Python module, not SQL

`scripts/seed_demo.py` uses the existing SQLAlchemy async session (same
models as production code). A raw SQL seed file would drift silently as
models change; a Python script fails loudly. The script is idempotent:
running it twice does not create duplicate data (it checks by slug/phone
before inserting).

---

## Tasks Overview

All tasks live on **one branch**: `feature/MOD-9-polish`.
One commit per task. One PR at the end of the phase.

| # | Task | What it delivers |
|---|------|-----------------|
| 9.1 | LICENSE + CONTRIBUTING.md + SECURITY.md | Repo is publicly releasable; contribution and security policies documented |
| 9.2 | For-reviewers guide | `docs/FOR_REVIEWERS.md` — all defend-it Q&A answered with code references |
| 9.3 | Demo seed script | `scripts/seed_demo.py` — one command populates the bakery demo tenant |
| 9.4 | Demo script + README final polish | `docs/DEMO_SCRIPT.md`; README gains "For Reviewers" + "Demo" sections; badges |
| 9.5 | Phase 9 memory update + CI guard | `tests/test_phase9_ci_guards.py`; memory files updated |

5 tasks. One branch, one commit per task, single PR at phase end.

---

## Task 9.1 — LICENSE + CONTRIBUTING.md + SECURITY.md

Make the repository publicly releasable with standard open-source governance files.

**What ships:**

- `LICENSE` (repo root): MIT License, year 2026, copyright holder "Ali Hamad".

- `CONTRIBUTING.md` (repo root):
  - **Prerequisites** — Docker, `uv`, Node 20+
  - **Dev setup** — `docker compose up`, `uv sync --dev`, `npm install`
  - **Running tests** — `uv run pytest -m "not integration and not load"`
  - **Branch naming** — `feature/MOD-{n}-{slug}` (mirrors existing convention)
  - **Commit format** — `type(scope): message` (mirrors existing commits)
  - **Before submitting a PR** — `uv run ruff check`, `uv run black --check`,
    `npm run lint`, `npm run typecheck`, tests green, no `os.getenv` / `print(`
    introduced
  - **Architecture rules** — pointer to `constitution.md` and The Wall constraint

- `SECURITY.md` (repo root):
  - Scope: what is and isn't in scope for reports
  - How to report: email address or GitHub private advisory
  - Response timeline: acknowledge within 72h, patch within 14 days for Critical
  - Hall of fame (empty placeholder)
  - Note: this is a dev/portfolio project; no bug bounty

**DoD:** three files present at repo root; `LICENSE` contains correct year and
name; `CONTRIBUTING.md` references `constitution.md`; `SECURITY.md` has a
contact method; `uv run pytest` still green.

---

## Task 9.2 — For-reviewers guide

A technical reviewer should be able to evaluate the project by reading one file,
then spot-checking the code references inside it.

**What ships:**

- `docs/FOR_REVIEWERS.md`:

  Sections mirror the defend-it questions from Phases 0–8. Each question gets:
  - The question (verbatim from the phase file)
  - A 2–5 sentence answer
  - A **→ Code** reference: `backend/app/path/to/file.py:line_range`

  Sections:
  1. **Foundation (Phase 0)** — why `migrate` is separate; where Gemini key lives;
     what `lifespan` does; `docker compose up` walk-through
  2. **The Wall (Phase 1)** — where tenant isolation is enforced; what's in the JWT;
     how `tenant_owners` differs from `users`; cross-tenant JOIN scoping
  3. **Order Flow (Phase 2)** — path from webhook to DB row; owner vs customer routing;
     why Tier 1 model for parsing; LangSmith trace walk-through
  4. **Dashboard (Phase 3)** — RTL layout; CORS; separate frontend container; polling vs WebSockets
  5. **Inventory & HIL (Phase 4)** — concurrent inventory deduction; HIL gate location;
     supplier webhook retry; approval fatigue
  6. **OCR Pipeline (Phase 5)** — Tesseract vs Cloud Vision decision; image storage;
     confidence threshold; knowledge base staleness
  7. **ML Layer (Phase 6)** — churn label definition; feature justification; why this
     classifier; model loading location; cold-start handling
  8. **Agent Supervisor (Phase 7)** — supervisor routing logic; idempotency on retries;
     checkpoint resume; cost of a morning briefing
  9. **Hardening (Phase 8)** — all three providers down; rate limiter key design;
     red-team pass-through; backup/restore procedure; cross-tenant load test isolation

**DoD:** `docs/FOR_REVIEWERS.md` exists; every Phase 0–8 defend-it question
addressed; every code reference points to a file that actually exists in the repo
(verified by grep); `uv run pytest` green.

---

## Task 9.3 — Demo seed script

A single command populates the stack with a realistic Lebanese bakery called
"مخبز أبو خالد" so the full Phase 2–8 flow can be demonstrated immediately.

**What ships:**

- `scripts/seed_demo.py`:

  Creates (idempotently — skips existing records by slug/phone check):

  **Tenant:** `مخبز أبو خالد` (Abu Khaled's Bakery)
  - WhatsApp number: `+96170000001` (test number)
  - Owner phone: `+96170000002`
  - Dashboard user: `demo@modir.test` / `DemoPassword1`

  **Business profile:**
  - Description: "أفضل كعك وبقلاوة في بيروت"
  - Location: "الحمرا، بيروت"
  - Delivery radius: 5 km
  - Accepts delivery + pickup

  **Products (8 items):**
  ```
  كعك بالسمسم     — 15,000 LBP   unit: حبة    category: مخبوزات
  بقلاوة بالفستق — 120,000 LBP  unit: كيلو   category: حلويات
  معروك رمضان    — 20,000 LBP   unit: حبة    category: مخبوزات
  كنافة          — 90,000 LBP   unit: كيلو   category: حلويات
  خبز صج        — 5,000 LBP    unit: ربطة   category: خبز
  تشريبة عيش    — 3,000 LBP    unit: حبة    category: خبز
  بزورة          — 50,000 LBP   unit: كيلو   category: مكسرات
  قريبة بالقشطة — 8,000 LBP    unit: حبة    category: حلويات
  ```

  **Operating hours:** Mon–Sat 07:00–21:00, closed Sunday, Ramadan note on every day.

  **Policies:**
  - `min_order_lbp = "50000"`
  - `delivery_fee_lbp = "10000"`
  - `payment_methods = "كاش، OMT، بنك"`
  - `rate_limit_rpm = "30"`
  - `daily_llm_budget_usd = "5.00"`

  **Customers (5):**
  - Each with a unique phone, display name in Arabic, `first_seen_at` spread over 30 days

  **Orders (20):**
  - Spread over the last 7 days, mixing products, quantities 1–5
  - `source` alternates between `"agent"` and `"manual"` (show both paths)
  - Statuses: `confirmed`, `preparing`, `ready`, `delivered`

  **`agent_runs` rows (30):**
  - 30 rows spread over 30 days, alternating agents, `cost_usd` 0.002–0.015
  - Provides data for the cost dashboard

  Usage:
  ```bash
  cd backend
  uv run python scripts/seed_demo.py
  # Output: "Demo tenant created: مخبز أبو خالد (tenant_id=...)"
  # or:     "Demo tenant already exists — updating products only"
  ```

- `scripts/seed_demo.py` is excluded from the `tests/` suite (it's a helper,
  not a test). It imports from `app/` using the same async engine setup as the
  test fixtures.

**DoD:** `uv run python scripts/seed_demo.py` completes without error on a fresh
DB; running it twice does not duplicate records; the dashboard at `localhost:5173`
shows the bakery data after seeding; orders appear in the order feed; cost
dashboard shows 30 days of spend; `uv run pytest` still green.

---

## Task 9.4 — Demo script + README final polish

Give reviewers and demo audiences a script to follow, and ensure the README
is the single authoritative "where do I start?" document.

**What ships:**

- `docs/DEMO_SCRIPT.md`:

  A step-by-step demo guide, organized as a narrative a presenter would follow.
  Assumes the seed script has been run.

  **Act 1 — Customer places an order (2 min)**
  - Use cURL (or the Telegram bot in dev) to POST a simulated WhatsApp webhook
    with Lebanese Arabic text: `"مرحبا، بدي ٣ كعكات بالسمسم وبقلاوة كيلو بكرا الصبح"`
  - Show the structured log output (tenant_id, tools fired, Arabic reply)
  - Open dashboard → Orders → see the new order arrive

  **Act 2 — Owner dashboard (1 min)**
  - Login as `demo@modir.test`
  - Show today's orders, customer list, cost dashboard (from seed data)
  - Show the AI status banner (briefly mock `/health/ai` to return false)

  **Act 3 — ML demand forecast (30 sec)**
  - POST to `/predictions/demand?product_id=<ka'ak_id>&days=7`
  - Show the JSON response with a 7-day forecast

  **Act 4 — Graceful degradation (30 sec)**
  - Set `MOCK_LLM_FAIL=1` (or explain the chaos test equivalent)
  - POST another customer message → observe the Arabic "unavailable" reply
  - Show the AI status banner in the frontend
  - Submit a manual order through the dashboard form

  **Act 5 — Observability (30 sec)**
  - `docker compose --profile observability up loki grafana`
  - Open Grafana → Modir dashboard → show request rate and cost panels
  - Run one more request, watch it appear in Loki explore view

  Each act has exact cURL commands (with the seeded demo data IDs filled in).

- `README.md` additions (edit, not replace):
  - New section **"For Reviewers"** (after Documentation links):
    ```
    ## For Reviewers
    - [docs/FOR_REVIEWERS.md](docs/FOR_REVIEWERS.md) — defend-it Q&A with code references
    - [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) — step-by-step demo guide
    - Seed demo data: `cd backend && uv run python scripts/seed_demo.py`
    ```
  - New section **"Demo"** (before Prerequisites):
    ```
    ## Demo
    After `docker compose up`, seed realistic bakery data:
    cd backend && uv run python scripts/seed_demo.py
    Then follow docs/DEMO_SCRIPT.md for the full 5-minute walkthrough.
    ```
  - CI badge: `![CI](https://github.com/<owner>/modir/actions/workflows/ci.yml/badge.svg)`
    (placeholder — user replaces `<owner>` with their GitHub username)

**DoD:** `docs/DEMO_SCRIPT.md` exists with exact cURL commands that work against
the seeded data; README renders on GitHub (Mermaid + badges); "For Reviewers"
section visible; `uv run pytest` green.

---

## Task 9.5 — Phase 9 CI guards + memory update

Close out the phase: lightweight CI guards prove Phase 9 artifacts exist,
and the project memory is updated.

**What ships:**

- `tests/test_phase9_ci_guards.py`:
  ```python
  def test_license_exists():
      assert Path("LICENSE").exists()

  def test_contributing_exists():
      assert Path("CONTRIBUTING.md").exists()

  def test_security_exists():
      assert Path("SECURITY.md").exists()

  def test_for_reviewers_exists():
      assert Path("docs/FOR_REVIEWERS.md").exists()

  def test_demo_script_exists():
      assert Path("docs/DEMO_SCRIPT.md").exists()

  def test_seed_demo_exists():
      assert Path("scripts/seed_demo.py").exists()

  def test_seed_demo_is_not_test_fixture():
      # seed_demo.py must not import pytest or be in the tests/ directory
      content = Path("scripts/seed_demo.py").read_text()
      assert "import pytest" not in content
  ```
  These run in the normal `pytest` suite (no special marker needed — they are
  file-system checks, not integration tests).

- `.specify/memory/phases/PHASE_9.md`: this file (already written — no action
  needed at Task 9.5, it was written before Task 9.1).

- Update `C:\Users\user\.claude\projects\...\memory\` files:
  - Update `phase6-workflow.md` (or equivalent) to reflect Phase 9 started
  - Update `phase4-plan-and-workflow.md` notes to reflect Phase 9 workflow:
    all tasks on ONE branch, single PR at phase end; commit+push per task

**DoD:** `tests/test_phase9_ci_guards.py` all pass; `uv run pytest` green;
CI green on push to `feature/MOD-9-polish`; PR description references all
5 tasks; memory files reflect Phase 9 is in progress.

---

## Phase 9 — Definition of Done

- [ ] `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md` present at repo root
- [ ] `docs/FOR_REVIEWERS.md` answers every Phase 0–8 defend-it question with
      a code reference that resolves to a real file
- [ ] `scripts/seed_demo.py` runs cleanly on a fresh DB; idempotent on a
      re-run; dashboard shows bakery data after seed
- [ ] `docs/DEMO_SCRIPT.md` provides exact commands for a 5-minute demo
- [ ] README has "Demo" and "For Reviewers" sections; CI badge present
- [ ] `tests/test_phase9_ci_guards.py` all pass
- [ ] `uv run pytest -m "not integration and not load"` green
- [ ] CI green on push to `feature/MOD-9-polish`

---

## Phase 9 — Defend-it questions

- Why MIT and not Apache 2.0?
- A new contributor wants to add a sixth agent. Walk them through the process
  from reading CONTRIBUTING.md to opening a PR.
- The seed script runs twice. How do you guarantee no duplicate records?
- A reviewer skips the demo and goes straight to `docs/FOR_REVIEWERS.md`.
  Can they verify every answer without running the code?
- What's in the demo data that makes the cost dashboard non-trivial to look at?
- Walk through the demo script end-to-end. Which act is most likely to fail on
  a fresh machine, and why?
