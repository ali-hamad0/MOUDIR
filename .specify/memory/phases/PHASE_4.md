# Phase 4 — Inventory & The First HIL Loop

> **Hand this file to Claude Code in VS Code with:**
> "Read `.specify/memory/constitution.md`, `.specify/memory/ROADMAP.md` (Phase 4),
> and this file. Implement Phase 4 task by task. Pause for approval after each task
> before committing."

> 🛑 **This is where Modir first does something with consequences.** A wrong
> purchase order costs Abu Khaled real money with a real supplier. Human-in-the-loop
> (constitution V) stops being a principle and becomes a hard gate in code. Build the
> HIL pattern correctly ONCE here — Phases 5 (bill approval) and 7 (customer
> re-engagement, finance actions) reuse this exact primitive. If the gate is sloppy
> here, it is sloppy everywhere after.

---

## Goal

When a customer's order is **completed**, Modir deducts the ordered quantities
from inventory in one atomic transaction. When a product crosses its low-stock
threshold, the **InventoryAgent drafts a purchase order** — and that PO does
**NOT** send. It lands in an **Approvals inbox** in the dashboard. Abu Khaled
approves (with a reason optional) or rejects (reason required). Only an approved
PO — carrying a **cryptographically signed approval token** — passes the single
execution gate and is dispatched to the supplier via a configurable webhook
(MailHog/log in dev), with retry + backoff and a manual-handling fallback. Every
PO action is in the audit log with who, when, and why.

By the end of Phase 4 you have **a running shop**: orders come in (Phase 2),
appear in the dashboard (Phase 3), draw down stock, and trigger a human-gated
reorder loop.

## Resist scope creep

- **No real ML.** `forecast_demand` is a deterministic placeholder (e.g.
  trailing-average over recent deductions, or a fixed reorder quantity). Real
  trained models are **Phase 6** — do not import scikit-learn here.
- **No OCR / no bill ingestion.** Inventory is adjusted by orders and by manual
  dashboard edits only. Supplier-bill OCR that *increases* stock is **Phase 5**.
- **No RAG.** No knowledge-base retrieval in the InventoryAgent this phase.
- **No supervisor.** The InventoryAgent is built as a standalone LangGraph graph
  (mirroring the Phase 2 OrderAgent) so Phase 7 can drop it under the supervisor
  without a rewrite — but Phase 4 does NOT wire a supervisor.
- **No real supplier integration / no real billing.** Dispatch is a webhook to a
  configurable URL; dev mode logs / hits MailHog. No external account needed.
- **No approval-fatigue tooling beyond the basics.** Bulk-approve, digest emails,
  SLA timers are later polish. (The defend-it question is still answered in prose.)

## Prerequisites

- [ ] Phase 3 is complete and merged to `main` (through PR #67). DoD met;
      defend-it write-up at `docs/PHASE_3_DEFEND_IT.md`.
- [ ] `cd backend && uv run pytest tests` is green (**87 tests**), including the
      wall-crossing, guardrail, dashboard-read, and onboarding suites.
- [ ] `docker compose up` brings up a clean, healthy stack (db, redis, vault,
      minio, mailhog, api, migrate, vault-seed) and the order flow produces a real
      order row + Lebanese-Arabic reply end-to-end.
- [ ] These already exist and Phase 4 builds ON them — **do not re-create**:
  - **The one `products` table** (`Product` in `app/db/models.py`) and
    `ProductRepository`. Phase 4 adds an `Inventory` table that *references*
    products — it does NOT add an "inventory_products" table (ROADMAP pitfall;
    constitution: one catalog table).
  - **`OrderService.create_order`** (`app/services/orders.py`) — the ONLY writer
    of orders. Phase 4 adds an order *completion* path that triggers deduction;
    it does not rewrite order creation. Note `Order.status` already exists
    (default `"confirmed"`); Phase 4 introduces a `"completed"` transition.
  - **`OrderEvent`** (per-order breadcrumb table) and **`AuditService.record`**
    (`tenant_id` + `actor_id` + `action` + `target`; flushes, does not commit —
    joins the caller's transaction). Every PO action audits through this.
  - **`TenantScopedRepository`** base (`app/repositories/base.py`) —
    `_require_tenant_scope` is the Wall. Every new repo extends it; new JOINs are
    scoped on both sides.
  - **The HIL UI seed:** the founder approvals screen + `admin.py`
    approve/reject pattern (list → action with reason → audited). The owner-facing
    **Approvals inbox** reuses this shape, but behind `get_current_user`
    (tenant-scoped), not `get_current_admin`.
  - **`EmailSender`** (`app/infra/email.py`, `httpx`/`aiosmtplib`, dev→MailHog) and
    the **Vault secret + dual seed-path** discipline (`secrets_map`,
    `seed_vault.sh` AND the `vault-seed` service must stay in sync). The supplier
    webhook dispatcher follows the same provider-agnostic, Vault-credentialed,
    dev-safe pattern.
  - **`LLMRouter`/`GeminiRouter`** (`app/agents/llm/router.py`) — `tier1()` is the
    only model the InventoryAgent's language step may use (drafting the PO note).
    The provider SDK stays confined to `app/agents/llm/`.
  - **The agent pattern:** Phase 2 `OrderAgent` is a compiled `StateGraph` built
    ONCE in `lifespan` (`app/main.py`), opening its own session per call from an
    injected `async_sessionmaker`, with the per-message `ToolContext` passed via
    the graph `config` (never stored on the instance — concurrency-safe). The
    InventoryAgent mirrors this exactly.
  - **`Settings`** (one pydantic-settings class) + `get_settings`. New non-secret
    Phase 4 config goes here; the supplier-webhook signing/HMAC reuses the
    Vault-resolved `jwt_secret` (no new secret needed unless we add a shared
    webhook auth header — decided in Task 4.10).
  - **Frontend:** the Phase 3 React+Vite+TS app (RTL, Arabic i18n dictionary,
    `apiClient` with JWT + 401 redirect, app shell with adaptive nav, `formatMoney`,
    dual-currency, skeleton/empty/error states). Phase 4 adds pages, not a new app.

## Core constraint reminder — The Wall + The Gate

Two non-negotiables govern this phase:

1. **The Wall (constitution I):** every new row carries `tenant_id`; every new
   repo method filters by it; the inventory-deduction JOIN (order_items → products
   → inventory) is scoped on every side. A test *tries* to cross and is blocked.
2. **The Gate (constitution V):** there is ONE execution gate. The supplier
   dispatcher refuses to send any PO that does not present a **valid signed
   approval token** bound to that PO id + approver + tenant. There is no "send
   anyway" code path — an accidental call without a token is rejected, not sent.

---

## Architecture decisions (recorded — read before building)

These were decided up front (some via explicit owner sign-off) so the tasks stay
coherent. When one conflicts with the constitution, the constitution wins.

- **Inventory is a new tenant-scoped table referencing `products`** — NOT a new
  catalog table. One `Inventory` row per `(tenant_id, product_id)`. It holds the
  live quantity, the reorder threshold, the reorder quantity, and an optional
  `supplier_id`. (Decision: "Columns on Inventory + a suppliers table.")
- **Suppliers are a small tenant-scoped table** (`suppliers`): name, dispatch
  type (`webhook` for now), webhook URL, optional contact email. A PO references a
  supplier. (Same decision.)
- **PO drafting is triggered INLINE on deduction** (Decision: "Inline on
  deduction"). When order completion drops a product to/below its threshold, the
  completion flow asks the InventoryAgent to draft a PO **in the same request**
  (no new worker/scheduler container this phase — Phase 5 introduces workers). The
  draft is *queued for approval*; it does not auto-send. A product that already
  has an open (draft/approved-unsent) PO is **not** re-drafted (idempotent on
  open-PO existence) to avoid approval spam.
- **Dispatch is a configurable webhook → MailHog/log in dev** (Decision:
  "Configurable webhook → MailHog/log"). The dispatcher reuses the
  provider-agnostic, Vault-credentialed, dev-safe shape of `EmailSender`: a
  `SupplierDispatcher` in `app/infra/` that POSTs the PO payload to the supplier's
  webhook via `httpx.AsyncClient` (never `requests`). Dev mode logs the payload /
  posts to a local catcher and never calls a real external supplier.
- **The HIL gate is a real signed approval token + central `ActionGate`**
  (Decision: "Real signed token + central gate"). On approval, the service mints
  an HMAC-signed token (using the Vault-resolved `jwt_secret`, same primitive as
  auth) over `(po_id, tenant_id, approver_user_id, action="po.dispatch")`. The
  `ActionGate.authorize(...)` verifies the token before the dispatcher runs. This
  is the reusable HIL primitive for Phases 5 and 7 — build it clean.
- **Dispatch fires AFTER commit and out-of-band of the approval request**
  (ROADMAP pitfall: never send the webhook synchronously inside the approval
  handler — a down supplier must not hang the approve call). Phase 4 has no Celery;
  we fire dispatch as an **`asyncio` background task** kicked off after the
  approval transaction commits, with retry + backoff inside it. (Document that
  Phase 8 may move this to a durable queue; an `asyncio` task is acceptable now
  because the PO row is the source of truth and a missed dispatch is recoverable
  from the manual queue.)
- **Retry budget → manual queue surfaced in the inbox** (Decision: "Surface in
  Approvals inbox as 'needs manual send'"). Dispatch retries with backoff up to
  `settings.po_dispatch_max_retries`; after the budget the PO becomes
  `dispatch_failed` and appears in the Approvals inbox flagged for manual handling
  (failure reason + "mark sent" / "retry" actions, all audited).
- **Atomic deduction with a DB-level guard against oversell** (ROADMAP pitfall:
  two orders for the last unit). Deduction is a single `UPDATE ... SET quantity =
  quantity - :n WHERE tenant_id = :t AND product_id = :p AND quantity >= :n`
  executed inside the completion transaction; affected-rowcount 0 means
  insufficient stock → a domain error, no negative quantity ever written. A
  `CHECK (quantity >= 0)` constraint backs it at the schema level.
- **Frontend: full slice, minimal UI** (Decision: "Full slice: backend + minimal
  UI"). Phase 4 ships an Inventory management view (levels + thresholds, low-stock
  indicator) and an Approvals inbox (approve/reject + manual-send), reusing Phase 3
  components and tokens. Honor the standing "don't over-polish the dashboard yet"
  preference — functional and RTL/Arabic-correct, not pixel-polished.

---

## New data model (read this before you build)

All Phase 4 tables are **tenant-scoped** (carry a non-nullable, indexed
`tenant_id`) and go through `TenantScopedRepository`. They reference the existing
`products` table — no new catalog table.

```
── Inventory (tenant-scoped) ──
inventory          — live stock per product, one row per (tenant_id, product_id)
                     (id, tenant_id, product_id → products.id,
                      quantity            INT  NOT NULL DEFAULT 0  CHECK (>= 0),
                      reorder_threshold   INT  NULL,   -- low-stock trip point
                      reorder_quantity    INT  NULL,   -- default qty to reorder
                      supplier_id         → suppliers.id  NULL,
                      created_at, updated_at)
                     UNIQUE (tenant_id, product_id)

suppliers          — who a reorder is sent to (tenant-scoped)
                     (id, tenant_id, name, dispatch_type DEFAULT 'webhook',
                      webhook_url NULL, contact_email NULL, is_active DEFAULT true,
                      created_at, updated_at)

── Purchase orders + HIL (tenant-scoped) ──
purchase_orders    — a drafted/approved/sent reorder (the HIL artifact)
                     (id, tenant_id, supplier_id → suppliers.id NULL,
                      product_id → products.id,
                      quantity            INT  NOT NULL,
                      status              -- see lifecycle below
                      draft_reason        TEXT NULL,   -- why the agent drafted it
                      agent_note_ar       TEXT NULL,   -- LLM-drafted supplier note (Arabic)
                      reviewed_by         → users.id NULL,   -- approver/rejecter
                      reviewed_at         NULL,
                      reject_reason       TEXT NULL,
                      dispatch_attempts   INT  DEFAULT 0,
                      dispatched_at       NULL,
                      dispatch_error      TEXT NULL,
                      created_at, updated_at)

purchase_order_events — per-PO breadcrumb trail (mirrors OrderEvent)
                     (id, tenant_id, purchase_order_id NULL, event, detail,
                      created_at)
```

**PO status lifecycle (single source of truth for the gate + UI):**

```
draft ──approve──► approved ──dispatch(signed token)──► sent
  │                   │                                   ▲
  │                   └──dispatch fails after retries──► dispatch_failed ──manual "mark sent"──┘
  └──reject──► rejected
```

- `draft` — agent proposed it; awaiting human. **Never dispatched.**
- `approved` — human approved; a signed token now exists; dispatch is queued.
- `sent` — dispatcher confirmed delivery (webhook 2xx / dev log).
- `dispatch_failed` — retry budget exhausted; in the manual queue.
- `rejected` — human declined; carries `reject_reason`. Provisions nothing.

> ⚠️ The status flag is the UI/lifecycle marker. It is **not** the gate. The gate
> is the **signed token check in `ActionGate`** — `status == "approved"` is a
> necessary but NOT sufficient condition for dispatch. A bug that flips a status
> must still not produce a send without a valid token. (This is the literal
> reading of constitution V the owner asked for.)

---

## The Phase 4 shape (what we wire end-to-end)

```
PART A — Inventory + atomic deduction (backend)

  Customer order (Phase 2) ──confirm──► Order(status=confirmed)
                                           │
  Owner marks order complete ─ POST /orders/{id}/complete ─┐
   (or auto on confirm — decided Task 4.4)                 ▼
                         OrderCompletionService (atomic txn):
                            for each line:
                              UPDATE inventory SET quantity = quantity - qty
                                WHERE tenant_id=? AND product_id=? AND quantity >= qty
                              rowcount 0 → InsufficientStock (domain error, rollback)
                            Order.status = "completed"; OrderEvent("completed")
                            audit "order.completed"
                            ── then, INLINE: for any product now ≤ threshold
                               with no open PO → InventoryAgent.draft_po(...)

PART B — InventoryAgent (standalone LangGraph graph, lifespan-built)

  draft_po(product, inventory, supplier):
     check_stock        → current level (tool, tenant-scoped)
     forecast_demand    → PLACEHOLDER reorder qty (no ML; Phase 6)
     draft_purchase_order → writes PO(status="draft") + agent_note_ar (tier1 LLM)
                            DOES NOT SEND. Audited "po.drafted".

PART C — HIL approval + the single execution gate (backend)

  Owner ─ GET  /approvals                 (tenant-scoped inbox: drafts + manual queue)
        ─ POST /approvals/{po_id}/approve  → status=approved
        │         └─ mint signed token (HMAC over po_id+tenant+approver+"po.dispatch")
        │            audit "po.approved"; commit; THEN fire dispatch task (background)
        ─ POST /approvals/{po_id}/reject {reason} → status=rejected, audited
        ─ POST /approvals/{po_id}/mark-sent       → manual close of dispatch_failed, audited

  SupplierDispatcher.dispatch(po, token):
     ActionGate.authorize(token, po) ──invalid/absent──► REFUSE (never sends)
                                     ──valid──► httpx POST supplier.webhook_url
                                                 (dev: log / MailHog)
                                       2xx → status=sent, dispatched_at, audit "po.sent"
                                       fail → backoff retry ≤ max → dispatch_failed,
                                              audit "po.dispatch_failed"

PART D — Frontend (minimal, reuses Phase 3)

  Inventory view  — levels + thresholds per product, low-stock badge, manual edit
  Approvals inbox — pending drafts (approve/reject with reason) + manual-send queue
```

Everything runs **async**; every tenant-scoped read/write takes `tenant_id` from
the authenticated JWT, never from the body. Every PO action is audited.

---

## Phase 4 — Tasks Overview

| Task | What | Branch |
|------|------|--------|
| **— Part A: Inventory model + deduction —** | | |
| 4.1 | `inventory` + `suppliers` models + migration (CHECK quantity≥0) | `feature/MOD-4-inventory-model` |
| 4.2 | `InventoryRepository` (atomic deduct, low-stock query) + `SupplierRepository` | `feature/MOD-4-inventory-repo` |
| 4.3 | Inventory CRUD API (list/upsert level+threshold, suppliers) + schemas | `feature/MOD-4-inventory-api` |
| 4.4 | `OrderCompletionService`: atomic deduction on order complete + `POST /orders/{id}/complete` | `feature/MOD-4-order-complete` |
| 4.5 | Deduction tests: atomicity, oversell race, tenant isolation | `feature/MOD-4-deduction-tests` |
| **— Part B: InventoryAgent —** | | |
| 4.6 | `purchase_orders` + `purchase_order_events` models + migration | `feature/MOD-4-po-model` |
| 4.7 | `PurchaseOrderRepository` + `PurchaseOrderService` (draft/approve/reject/dispatch state) | `feature/MOD-4-po-service` |
| 4.8 | InventoryAgent graph + 3 tools (check_stock, forecast_demand placeholder, draft_purchase_order); Arabic prompt; lifespan wiring | `feature/MOD-4-inventory-agent` |
| 4.9 | Wire inline PO drafting into `OrderCompletionService` (idempotent on open PO) | `feature/MOD-4-draft-on-low-stock` |
| **— Part C: HIL gate + dispatch —** | | |
| 4.10 | `ActionGate` + signed approval token (HMAC via Vault `jwt_secret`) | `feature/MOD-4-action-gate` |
| 4.11 | `SupplierDispatcher` (httpx webhook, dev→MailHog/log, retry+backoff, manual fallback) + Vault/Settings | `feature/MOD-4-supplier-dispatch` |
| 4.12 | Approvals API: `GET /approvals`, approve/reject/mark-sent (tenant-scoped, audited, gate-enforced) | `feature/MOD-4-approvals-api` |
| 4.13 | HIL tests: no-send-without-token, approve→dispatch, reject, retry→manual, audit, Wall | `feature/MOD-4-hil-tests` |
| **— Part D: Frontend —** | | |
| 4.14 | Inventory view (levels, thresholds, low-stock badge, manual edit) | `feature/MOD-4-inventory-ui` |
| 4.15 | Approvals inbox (drafts approve/reject + manual-send queue) | `feature/MOD-4-approvals-ui` |
| **— Close-out —** | | |
| 4.16 | CI: new suites run; forbidden-patterns clean (httpx not requests; SDK confined); frontend build green | `chore/MOD-4-ci` |

Each task is a separate branch and PR. No exceptions. **Pause for approval after each.**

> **Why deduction before the agent (recorded):** the InventoryAgent's whole reason
> to exist is "stock got low," and stock only moves once deduction works. Build the
> physical truth (Part A) first, then the agent that reacts to it (Part B), then the
> human gate that governs its consequences (Part C). The UI (Part D) comes last so
> it renders a system that already behaves correctly.

---

## Task 4.1 — `inventory` + `suppliers` Models + Migration

**Branch:** `feature/MOD-4-inventory-model`

Add two tenant-scoped tables to `app/db/models.py`, after the Phase 2 order models.

- `Inventory`: `tenant_id` (FK tenants, indexed), `product_id` (FK products,
  indexed), `quantity` (Integer, NOT NULL, default 0), `reorder_threshold`
  (Integer, nullable), `reorder_quantity` (Integer, nullable), `supplier_id` (FK
  suppliers, nullable). `UniqueConstraint(tenant_id, product_id)`. Add a
  `CheckConstraint("quantity >= 0", name="ck_inventory_qty_nonneg")` — the
  schema-level backstop against oversell (ROADMAP pitfall).
- `Supplier`: `tenant_id` (FK tenants, indexed), `name` (String, NOT NULL),
  `dispatch_type` (String(16), NOT NULL, default `"webhook"`), `webhook_url`
  (String(1024), nullable), `contact_email` (String(320), nullable), `is_active`
  (Boolean, NOT NULL, default True).

Each class gets a docstring explaining the Wall scoping and why inventory
references the ONE products table rather than copying it (ROADMAP pitfall).
Autogenerate the migration, **review it by hand** (confirm the CHECK constraint,
FKs, indexes, and the unique constraint are present), round-trip upgrade/downgrade.

**Commit message:**
```
feat(models): inventory + suppliers tables (tenant-scoped) + migration

One inventory row per (tenant_id, product_id) referencing the single products
table — never a per-phase catalog copy. quantity is CHECK (>= 0) so the database
itself refuses an oversell. suppliers holds the reorder dispatch target. Migration
autogenerated, reviewed, round-trips.
```

**Verification:**
- `from app.db.models import Inventory, Supplier` imports clean.
- `alembic upgrade head` then `downgrade -1` round-trips on a fresh DB.
- The CHECK constraint exists (inspect the migration / `\d inventory` in psql);
  a manual `UPDATE inventory SET quantity = -1` is rejected by Postgres.
- Both tables have a non-nullable indexed `tenant_id`.

---

## Task 4.2 — `InventoryRepository` (Atomic Deduct, Low-Stock Query) + `SupplierRepository`

**Branch:** `feature/MOD-4-inventory-repo`

Extend `TenantScopedRepository` (base CRUD already gives get/list/add/delete,
each tenant-scoped). Add to `app/repositories/inventory.py`:

- `get_by_product(tenant_id, product_id) -> Inventory | None` (tenant-scoped).
- `deduct(tenant_id, product_id, qty) -> bool` — the atomic guard. Issue a single
  `update(Inventory).where(tenant_id==, product_id==, quantity >= qty).values(
  quantity = Inventory.quantity - qty)` and return `rowcount == 1`. **Do not**
  read-then-write (that races); the `quantity >= qty` predicate + the DB CHECK is
  the concurrency guard. `False` means insufficient stock → caller raises a domain
  error and the transaction rolls back.
- `list_low_stock(tenant_id) -> Sequence[Inventory]` — rows where
  `reorder_threshold IS NOT NULL AND quantity <= reorder_threshold`.
- `list_with_product(tenant_id, *, limit, offset)` — inventory joined to products
  for the dashboard view; JOIN scoped on BOTH sides by `tenant_id` (constitution I).

`SupplierRepository` in `app/repositories/suppliers.py`: base CRUD is enough plus
`list(tenant_id)`.

Add a domain error `InsufficientStock(product_id)` to `app/domain/errors.py`
(mirror the existing `ProductNotInCatalog` / `ProductUnavailable` shape).

**Commit message:**
```
feat(repo): inventory repository — atomic deduct + low-stock query

deduct() is a single guarded UPDATE (quantity >= qty) so two concurrent orders for
the last unit can never both succeed — the loser gets rowcount 0, no negative
write. list_low_stock surfaces reorder candidates; list_with_product joins
products scoped on both sides (a cross-tenant JOIN is a Sev-1 leak). Adds
InsufficientStock domain error.
```

**Verification:**
- Unit test: seed quantity 5, `deduct(.., 3)` → True, level 2; `deduct(.., 5)` →
  False, level unchanged.
- `list_low_stock` returns only rows at/below a set threshold; ignores rows with a
  null threshold.
- A `deduct` for another tenant's product (wrong tenant_id) affects nothing.

---

## Task 4.3 — Inventory CRUD API + Schemas

**Branch:** `feature/MOD-4-inventory-api`

The owner manages stock manually (ROADMAP: "Manual inventory CRUD in the
dashboard"). All tenant-scoped via `get_current_user` → `user.tenant_id`, never
the body.

- `app/api/schemas/inventory.py`: `InventoryRead` (product name_ar/name_en,
  quantity, reorder_threshold, reorder_quantity, supplier_id, is_low flag),
  `InventoryUpsert` (quantity, reorder_threshold, reorder_quantity, supplier_id),
  `InventoryPage`, plus `SupplierRead` / `SupplierUpsert`.
- `app/api/orders.py` style → new `app/api/inventory.py`:
  - `GET /inventory` (paginated, joined to product, low-stock flag computed).
  - `PUT /inventory/{product_id}` — upsert the inventory row for a product the
    tenant owns (404 if the product isn't this tenant's — scoped lookup).
  - `GET /suppliers`, `POST /suppliers`, `PUT /suppliers/{id}`.
- Mount the router in `app/main.py::create_app()`.
- Audit a manual level change (`inventory.adjusted`) and supplier changes.

**Commit message:**
```
feat(api): inventory + suppliers CRUD — tenant-scoped, audited

GET /inventory (paginated, joined to the catalog, low-stock flagged) and PUT
/inventory/{product_id} let the owner set levels and reorder thresholds; suppliers
CRUD configures the reorder target. tenant_id comes from the JWT, never the body;
manual adjustments are audited.
```

**Verification (live stack):**
- Set a level + threshold for a product → reflected in `GET /inventory`.
- Tenant A's `GET /inventory` never shows tenant B's products.
- Upsert against another tenant's product id → 404 (scoped lookup), no write.

---

## Task 4.4 — `OrderCompletionService` + `POST /orders/{id}/complete`

**Branch:** `feature/MOD-4-order-complete`

Deduction happens on **completion**, not confirmation — an order can be confirmed
and later cancelled before fulfillment; stock should move when the shop actually
fulfills it. The owner marks completion from the dashboard (Phase 4 adds the
endpoint; the button lands in Task 4.14).

- `app/services/order_completion.py` → `OrderCompletionService.complete(tenant_id,
  order_id, actor_id)`:
  - Load the order tenant-scoped (404 if not this tenant's / not found).
  - Reject if not in `confirmed` status (idempotent: completing an already-
    completed order is a 409 or a no-op — decide and document; prefer 409).
  - In ONE transaction: for each `order_item`, call `InventoryRepository.deduct`.
    If any returns False → raise `InsufficientStock`, roll back the whole thing
    (no partial deduction). For a product with **no inventory row**, decide and
    document: Phase 4 treats "no inventory row" as untracked → skip deduction for
    that line (log it), rather than block fulfillment. (Owners may not track every
    SKU yet.)
  - Set `Order.status = "completed"`, write `OrderEvent(event="completed")`,
    `AuditService.record(action="order.completed")`, commit.
  - **Then** (Task 4.9 wires this) trigger low-stock PO drafting — left as a clearly
    marked hook in this task, implemented in 4.9.
- `POST /orders/{id}/complete` in `app/api/orders.py` (tenant-scoped, returns the
  updated order). Map `InsufficientStock` → 409 with a clear message.

**Commit message:**
```
feat(orders): atomic inventory deduction on order completion

POST /orders/{id}/complete moves a confirmed order to completed and deducts every
tracked line from inventory in one transaction — any insufficient line rolls the
whole completion back (no partial deduction). Untracked products are skipped and
logged. Completion is audited; the low-stock reorder hook is wired in 4.9.
```

**Verification (live stack):**
- Seed product with inventory 20; a completed order for 5 → level 15, order
  `completed`, audit row present.
- Complete an order that needs more than is in stock → 409, level unchanged,
  status still `confirmed` (full rollback).
- Completing an already-completed order → 409 (documented behavior).

---

## Task 4.5 — Deduction Tests (Atomicity, Oversell Race, Tenant Isolation)

**Branch:** `feature/MOD-4-deduction-tests`

**File: `backend/tests/test_inventory_deduction.py`** — assert:
1. Completion deducts the right quantity for each line; order becomes `completed`.
2. **Oversell guard:** with the last unit in stock, two completions racing for it —
   exactly one succeeds, one gets `InsufficientStock`; the level never goes
   negative (assert via the guarded UPDATE rowcount path; simulate concurrency
   with two sessions if the harness supports it, else assert the guard directly).
3. Partial-failure rollback: a multi-line order where one line is short →
   nothing is deducted, status unchanged.
4. **The Wall:** completing tenant A's order never touches tenant B's inventory;
   `deduct` with B's product_id under A's scope affects nothing.
5. Untracked product (no inventory row) → line skipped, completion still succeeds.

**Commit message:**
```
test(inventory): deduction atomicity, oversell race, and tenant isolation

Completion deducts correctly; two completions racing for the last unit yield
exactly one success and never a negative level; a short line rolls the whole
completion back; deduction stays inside the tenant's scope. The oversell guard is
the DB-level guarded UPDATE, not a read-then-write.
```

**Verification:**
- `cd backend && uv run pytest tests/test_inventory_deduction.py -v` green.
- Temporarily weaken `deduct` to a read-then-write → the oversell test FAILS
  (confirm, then revert).

---

## Task 4.6 — `purchase_orders` + `purchase_order_events` Models + Migration

**Branch:** `feature/MOD-4-po-model`

Add the HIL artifact tables to `app/db/models.py` (tenant-scoped). Fields per the
model section: `PurchaseOrder` (tenant_id, supplier_id nullable, product_id,
quantity, status default `"draft"`, draft_reason, agent_note_ar, reviewed_by →
users.id nullable, reviewed_at, reject_reason, dispatch_attempts default 0,
dispatched_at, dispatch_error) and `PurchaseOrderEvent` (mirrors `OrderEvent`:
tenant_id, purchase_order_id nullable, event, detail).

Document the status lifecycle in the class docstring (the diagram above), and note
explicitly that **status is the lifecycle marker, not the security gate** — the
gate is the signed token (Task 4.10). Autogenerate, **review by hand**,
round-trip.

**Commit message:**
```
feat(models): purchase_orders + purchase_order_events (tenant-scoped) + migration

The HIL artifact: a PO moves draft → approved → sent (or dispatch_failed →
manual), or draft → rejected. status is the lifecycle marker for the UI; the
actual send gate is the signed approval token (4.10), so a flipped status alone
can never dispatch. Per-PO event trail mirrors OrderEvent. Migration reviewed,
round-trips.
```

**Verification:**
- `from app.db.models import PurchaseOrder, PurchaseOrderEvent` imports clean.
- `alembic upgrade head` / `downgrade -1` round-trips.
- Both tables carry a non-nullable indexed `tenant_id`.

---

## Task 4.7 — `PurchaseOrderRepository` + `PurchaseOrderService`

**Branch:** `feature/MOD-4-po-service`

- `app/repositories/purchase_orders.py` (extends base): `list_for_inbox(tenant_id,
  *, statuses, limit, offset)` (drafts + dispatch_failed for the inbox),
  `has_open_po_for_product(tenant_id, product_id) -> bool` (draft or approved-unsent
  — drives idempotent drafting in 4.9), joined to product/supplier for display
  (JOINs scoped both sides).
- `app/services/purchase_orders.py` → `PurchaseOrderService` is the ONLY writer of
  PO state (mirrors `OrderService` being the only order writer):
  - `draft(tenant_id, product_id, supplier_id, quantity, reason, agent_note_ar)` →
    PO(status="draft"), `PurchaseOrderEvent("drafted")`, audit `po.drafted`.
  - `approve(tenant_id, po_id, approver_id)` → status `approved`, reviewed_by/at,
    event, audit `po.approved`. **Returns the PO; the signed token is minted in the
    API layer via `ActionGate` (4.10) and dispatch is fired after commit (4.12).**
  - `reject(tenant_id, po_id, approver_id, reason)` → status `rejected`, reason,
    audit `po.rejected`.
  - `mark_dispatched(tenant_id, po_id)` / `mark_dispatch_failed(tenant_id, po_id,
    error)` / `mark_sent_manually(tenant_id, po_id, actor_id)` — state + event +
    audit for the dispatch outcomes.
  - Each transition validates the **current** status (can't approve a non-draft,
    can't reject a sent PO) → 409 on a bad transition.

**Commit message:**
```
feat(po): purchase-order repository + service (the only PO-state writer)

PurchaseOrderService owns every PO transition (draft/approve/reject/dispatch
outcomes), each validating the current status and audited. The repo surfaces the
inbox (drafts + manual queue) and answers has_open_po_for_product so the agent
never re-drafts a product that already has one pending. Token minting + dispatch
live above this (4.10/4.12).
```

**Verification:**
- Draft → approve → mark_dispatched walks the status machine; a bad transition
  (approve an already-sent PO) → 409.
- `has_open_po_for_product` is True for a draft/approved-unsent PO, False after
  reject/sent.
- All transitions write an audit row.

---

## Task 4.8 — InventoryAgent Graph + Three Tools + Arabic Prompt + Lifespan Wiring

**Branch:** `feature/MOD-4-inventory-agent`

Mirror the Phase 2 `OrderAgent` exactly: a compiled `StateGraph` built ONCE, a
per-call `ToolContext` (tenant-scoped session + identity + router + settings)
passed via graph `config`, the single instance stored on `app.state` and built in
`lifespan`. Tools live in `app/agents/inventory/tools.py`; graph in
`app/agents/inventory/agent.py`; schemas in `app/agents/inventory/schemas.py`.

The three tools (ROADMAP names — exactly these):
- `check_stock(ctx, product_id)` — read the current `Inventory` level (tenant-
  scoped). No LLM.
- `forecast_demand(ctx, product_id)` — **PLACEHOLDER, no ML** (constitution IV:
  ML is Phase 6, and the placeholder is explicitly allowed here). Return a simple
  deterministic suggested quantity: `reorder_quantity` if set, else a documented
  trailing heuristic (e.g. recent deductions × a factor) or a fixed default.
  Clearly comment that Phase 6 replaces this with a trained model behind the same
  signature.
- `draft_purchase_order(ctx, product_id, suggested_qty)` — compose a short Arabic
  supplier note via `ctx.router.tier1()` (Tier 1 work — Flash, not Pro; ROADMAP
  pitfall), validate the LLM output with Pydantic (bad output → retry, not crash,
  same pattern as `parse_order`), then call `PurchaseOrderService.draft(...)`. It
  **writes a draft only** — it never sends.

Prompt: `backend/prompts/inventory_agent_ar.py` (all Arabic copy in files, never
inline — constitution + the established `prompts/` discipline). The agent exposes a
`draft_for_low_stock(tenant_id, product_id)` entry the completion flow calls.

Wire `app.state.inventory_agent = InventoryAgent(router, settings, sessionmaker)`
in `lifespan` next to the OrderAgent.

**Commit message:**
```
feat(agent): InventoryAgent — check_stock, forecast_demand (placeholder), draft_po

A standalone LangGraph graph mirroring the OrderAgent (built once in lifespan,
per-call ToolContext via config, concurrency-safe). forecast_demand is a
documented deterministic placeholder — real ML is Phase 6, behind this same
signature. draft_purchase_order writes a draft PO with a Tier-1 Arabic supplier
note and NEVER sends. Prompt copy lives in prompts/inventory_agent_ar.py.
```

**Verification (live stack):**
- Call `draft_for_low_stock` for a low product → a `draft` PO exists with a
  sensible quantity and an Arabic note; **no dispatch happened** (status `draft`,
  `dispatched_at` null).
- A malformed LLM note response retries and still produces a draft (the note may
  fall back to a templated Arabic string), never a 500.
- `grep -rn "import google\|langchain_google" backend/app/` shows the provider SDK
  only under `app/agents/llm/` (the agent uses the router, not the SDK directly).

---

## Task 4.9 — Wire Inline PO Drafting into `OrderCompletionService`

**Branch:** `feature/MOD-4-draft-on-low-stock`

Implement the hook left in Task 4.4. After a successful completion+deduction
commit, for each product now at/below its `reorder_threshold` (use
`InventoryRepository.list_low_stock` or check per deducted line):
- Skip if `has_open_po_for_product` is already True (idempotent — no approval spam).
- Otherwise call `inventory_agent.draft_for_low_stock(tenant_id, product_id)`.

Drafting runs **after** the completion transaction commits (a draft failure must
not roll back a legitimate fulfillment — the order is already complete; a missed
draft is recoverable, the threshold trips again next time). Log a `po.drafted`
breadcrumb. The agent is reached via `app.state.inventory_agent` (injected the same
way the dispatcher reaches the OrderAgent).

**Commit message:**
```
feat(inventory): draft a reorder PO inline when completion drops stock low

After an order completes and stock is deducted, any product now at/below its
reorder threshold gets a draft PO from the InventoryAgent — unless one is already
open (idempotent, no approval spam). Drafting runs after the completion commit so a
draft hiccup never rolls back a real fulfillment. The PO is queued for approval,
never sent.
```

**Verification (live stack):**
- Set threshold 5, level 6; complete an order for 2 → level 4 → a `draft` PO
  appears for that product, still unsent.
- Complete another small order for the same product → **no second draft** (open PO
  already exists).
- Reject the draft, drop stock again → a new draft is allowed.

---

## Task 4.10 — `ActionGate` + Signed Approval Token

**Branch:** `feature/MOD-4-action-gate`

The single execution gate (constitution V), built as a reusable primitive in
`app/infra/action_gate.py` (Phases 5/7 reuse it):
- `mint_approval_token(settings, *, action, resource_id, tenant_id, approver_id) ->
  str` — an HMAC-signed token (reuse the Vault-resolved `settings.jwt_secret`, the
  same signing primitive as `app/infra/security.py`; a short, dedicated claim set:
  `act`, `rid`, `tid`, `sub`, `exp`). Time-boxed (e.g.
  `settings.approval_token_ttl_minutes`, new typed Settings field).
- `ActionGate.authorize(settings, token, *, action, resource_id, tenant_id) ->
  ApprovedAction` — verifies signature, expiry, and that the token's
  action/resource/tenant match the requested dispatch. On any mismatch or missing
  token it raises `UnauthorizedAction` — there is **no** code path that dispatches
  without passing this.

Document at the top of the module that this is THE gate: any future executing
action (send PO, send re-engagement message, post finance entry) must obtain a
token on human approval and present it here before side-effecting.

**Commit message:**
```
feat(hil): ActionGate + signed approval token — the single execution gate

mint_approval_token signs (HMAC, Vault jwt_secret) an action over its
resource+tenant+approver+expiry; ActionGate.authorize verifies it before any
side-effecting dispatch. There is no bypass: an absent or mismatched token raises
UnauthorizedAction. This is the reusable HIL primitive constitution V demands —
Phases 5 and 7 reuse it.
```

**Verification:**
- A token minted for `(po.dispatch, po_id, tenant)` authorizes that exact
  dispatch; reusing it for a different po_id / tenant / action → `UnauthorizedAction`.
- An expired token → rejected. A tampered token (flipped byte) → rejected.
- Unit tests cover mint→authorize happy path and every rejection branch.

---

## Task 4.11 — `SupplierDispatcher` (httpx Webhook, Dev→MailHog/Log, Retry+Backoff)

**Branch:** `feature/MOD-4-supplier-dispatch`

`app/infra/supplier_dispatch.py` — provider-agnostic, dev-safe, mirroring
`EmailSender`:
- `SupplierDispatcher.dispatch(po, supplier, token)`:
  - **First**, `ActionGate.authorize(...)` (4.10) — refuse if the token is
    invalid/absent. (Belt-and-suspenders: the API layer only calls dispatch with a
    freshly minted token, but the dispatcher independently re-checks — the gate is
    enforced at the boundary that actually side-effects.)
  - Build the PO payload (product, qty, supplier, tenant ref, Arabic note).
  - Mode: `dev` logs the payload / posts to a local catcher (or emails via the
    existing `EmailSender` to MailHog) — **never** a real external supplier;
    `webhook` mode POSTs to `supplier.webhook_url` via `httpx.AsyncClient` (never
    `requests`). Any shared webhook auth header/secret resolves from **Vault**
    (add to `secrets_map` + BOTH seed paths if introduced).
  - Retry with exponential backoff up to `settings.po_dispatch_max_retries`. On
    success → `PurchaseOrderService.mark_dispatched`. After the budget →
    `mark_dispatch_failed(error=...)` (lands in the manual queue, 4.12).
- New typed Settings: `po_dispatch_max_retries`, `po_dispatch_backoff_seconds`,
  and (if a shared secret is used) the Vault-resolved field.

**Commit message:**
```
feat(infra): supplier dispatcher — gated httpx webhook with retry, dev-safe

dispatch() authorizes the signed approval token through ActionGate before it
side-effects, then POSTs the PO to the supplier webhook via httpx (never requests),
retrying with backoff. Dev mode logs / hits MailHog and never calls a real
supplier; any webhook secret resolves from Vault. Exhausting the retry budget marks
the PO dispatch_failed for the manual queue.
```

**Verification (live stack):**
- A valid token + a reachable (stub/local) webhook → PO `sent`, `dispatched_at`
  set, audit `po.sent`.
- A token mismatch → dispatcher refuses, nothing sent.
- A failing webhook → retries then `dispatch_failed` with the error recorded.
- `grep -rn "os.getenv\|print(\|import requests" backend/app/` still clean.

---

## Task 4.12 — Approvals API: `GET /approvals`, Approve / Reject / Mark-Sent

**Branch:** `feature/MOD-4-approvals-api`

The owner's HIL control surface (tenant-scoped via `get_current_user`, all
audited) — the inbox the founder-approvals screen seeded conceptually, now for the
owner:
- `GET /approvals` — paginated inbox: pending `draft` POs + `dispatch_failed`
  (manual queue), joined to product/supplier for display, status filter.
- `POST /approvals/{po_id}/approve` — `PurchaseOrderService.approve(...)`; on
  commit, **mint the signed token** (4.10) and **fire dispatch as a background
  asyncio task** (`SupplierDispatcher.dispatch`) — never inline/blocking inside the
  handler (ROADMAP pitfall: a down supplier must not hang the approve call). Return
  immediately with status `approved`.
- `POST /approvals/{po_id}/reject` — reason required; `reject(...)`.
- `POST /approvals/{po_id}/mark-sent` — manual close of a `dispatch_failed` PO
  (owner sent it out of band); audited `po.sent_manually`.
- Schemas in `app/api/schemas/approvals.py`. Mount the router in `create_app`.

**Commit message:**
```
feat(api): approvals inbox — approve/reject/mark-sent, gate-enforced, audited

GET /approvals is the owner's tenant-scoped HIL inbox (pending drafts + the manual
queue). Approve mints a signed token and fires supplier dispatch as a background
task (never blocking the handler on a slow supplier); reject requires a reason;
mark-sent closes a dispatch_failed PO out of band. Every action is audited.
```

**Verification (live stack):**
- Owner sees a drafted PO in `GET /approvals`; approving it → status `approved`,
  then (background) `sent`; the approve call returns fast even if the webhook is
  slow.
- Reject without a reason → 422; with a reason → `rejected`, recorded.
- A `dispatch_failed` PO appears in the inbox; `mark-sent` closes it, audited.
- Tenant A's inbox never shows tenant B's POs.

---

## Task 4.13 — HIL Tests (No-Send-Without-Token, Approve→Dispatch, Reject, Retry→Manual, Audit, Wall)

**Branch:** `feature/MOD-4-hil-tests`

**File: `backend/tests/test_hil_purchase_orders.py`** — assert:
1. **The gate holds:** calling `SupplierDispatcher.dispatch` with no token / a
   forged token / a token for a different PO → refused, PO never `sent`. (The
   constitution-V "accidental call without authorization is rejected" test.)
2. **Happy path:** draft → approve → (token minted) → dispatch → `sent`, with the
   webhook stubbed; `dispatched_at` set.
3. **Reject path:** reject → `rejected` + reason; never dispatched; provisions
   nothing.
4. **Retry → manual:** a webhook that always fails → retries exhaust →
   `dispatch_failed`, appears in the inbox query; `mark-sent` closes it.
5. **No auto-send:** an agent-drafted PO is `draft` and was never dispatched
   without an approval (assert dispatch is not reachable from drafting).
6. **The Wall:** tenant A's owner cannot see or approve tenant B's PO (scoped
   lookup → 404); the dispatch JOINs stay tenant-scoped.
7. **Audit:** draft, approve, reject, sent, dispatch_failed, sent_manually each
   produce an audit row.

Stub the webhook (no real network) and mock the LLM note (the suite stays offline,
like the existing tests).

**Commit message:**
```
test(hil): no send without a signed token; approve→dispatch; reject; retry→manual

Proves the single execution gate: dispatch refuses an absent/forged/mismatched
token and never sends. Happy path approves → dispatches → sent; reject provisions
nothing; an always-failing webhook exhausts retries to dispatch_failed and the
manual queue; the founder/owner Wall holds; every PO action is audited. Webhook
stubbed, LLM mocked.
```

**Verification:**
- `cd backend && uv run pytest tests/test_hil_purchase_orders.py -v` green.
- Temporarily make the dispatcher skip `ActionGate.authorize` → the no-send-without-
  token test FAILS (confirm, then revert). This is the proof the gate is real.

---

## Task 4.14 — Inventory View (Frontend)

**Branch:** `feature/MOD-4-inventory-ui`

Reuse the Phase 3 app shell, `apiClient`, i18n dictionary, tokens, and
`formatMoney`. A new Inventory page:
- List products with quantity, reorder threshold, reorder quantity, supplier; a
  **low-stock badge** (color + icon + Arabic label, never color alone — a11y rule
  from Phase 3) when at/below threshold.
- Inline edit (or a small modal) to set quantity / threshold / reorder qty /
  supplier → `PUT /inventory/{product_id}`; loading→success/error states, labeled
  inputs, blur validation (the Phase 3 form conventions).
- Mobile card layout at 360px / table ≥1024px (the `data-table` pattern Phase 3
  used for customers). All Arabic strings via the i18n dictionary.
- A **"mark complete"** action on an order in the existing order feed →
  `POST /orders/{id}/complete` (this is what triggers deduction + drafting), with a
  confirmation and a success/error toast.

Honor the standing "don't over-polish the dashboard yet" preference — functional,
RTL, Arabic-correct; not a visual redesign.

**Commit message:**
```
feat(frontend): inventory view + order "mark complete" action

Reuses the Phase 3 shell, i18n, and formatMoney: an inventory page lists each
product's level/threshold/reorder qty/supplier with a low-stock badge
(color+icon+label), editable via PUT /inventory. A "mark complete" action on an
order calls POST /orders/{id}/complete — the trigger for deduction and reorder
drafting. Cards at 360px, table on desktop; all copy from the dictionary.
```

**Verification:**
- Set a level/threshold in the UI → persisted; low-stock badge shows when at/below.
- "Mark complete" on a real order → level drops; readable and usable at 360px, RTL.

---

## Task 4.15 — Approvals Inbox (Frontend)

**Branch:** `feature/MOD-4-approvals-ui`

The owner-facing HIL inbox, modeled on the Phase 3 founder-approvals screen (reuse
its list/badge/reason-required shape) but behind the tenant-owner login:
- A list from `GET /approvals`: each pending draft PO shows product, suggested
  quantity, supplier, the agent's Arabic note, and a status badge.
- **Approve** (optional note) and **Reject** (reason required — mirror the founder
  reject UX) → `POST /approvals/{id}/approve` / `/reject`. Optimistic-ish UI with
  loading→success/error; the row reflects the new status (and moves to `sent` once
  dispatch completes on the next poll).
- A **manual queue** section for `dispatch_failed` POs with the failure reason and
  a **"mark sent" / retry** action.
- Empty state ("ما في طلبات شراء بانتظار الموافقة"), skeleton on load. Arabic via
  the i18n dictionary; 360px-first.

**Commit message:**
```
feat(frontend): owner approvals inbox — approve/reject reorders + manual queue

The tenant-owner HIL screen (modeled on the founder approvals UI): pending draft
POs with the agent's Arabic note, approve (optional note) / reject (reason
required), and a manual-send section for dispatch_failed POs. Status badges,
empty/skeleton states, RTL, all copy from the dictionary; works at 360px.
```

**Verification (live demo end-to-end):**
- Complete an order that trips low stock → a draft PO appears in the inbox →
  approve it → it dispatches (MailHog/log) and flips to `sent` within a poll.
- Reject requires a reason; a failed dispatch shows in the manual queue with
  "mark sent".

---

## Task 4.16 — CI: New Suites + Forbidden Patterns + Frontend Build

**Branch:** `chore/MOD-4-ci`

**Edit `.github/workflows/ci.yml`:**
- The backend job picks up `test_inventory_deduction.py`,
  `test_hil_purchase_orders.py` under the existing `uv run pytest backend/tests`
  step (LLM mocked; live smoke deselected as before; webhook stubbed).
- Reaffirm forbidden-patterns: no `os.getenv` / `print(` / `import requests` in
  `backend/app/`; the new `supplier_dispatch.py` uses `httpx`, not `requests`; the
  provider SDK stays confined to `app/agents/llm/` (the InventoryAgent uses the
  router). The single documented LangSmith `os.environ` write remains the only
  exception.
- The frontend job (already added in 3.21) covers the new Inventory + Approvals
  pages via `tsc --noEmit` + `vite build` — confirm it stays green.

**Commit message:**
```
ci: run Phase 4 inventory + HIL suites; reaffirm forbidden patterns

The backend job now runs the deduction and HIL purchase-order suites (LLM mocked,
webhook stubbed). Reaffirms no os.getenv/print/import requests in backend/app;
supplier_dispatch.py uses httpx; the provider SDK stays in app/agents/llm/. The
frontend job builds the new inventory and approvals pages. A regression fails the
build.
```

**Verification:**
- Push the branch; CI runs the full backend suite + the frontend build — both green.
- Introduce `import requests` in `supplier_dispatch.py` → CI fails; revert → green.

---

## Phase 4 — Definition of Done

**Inventory + deduction:**
- [ ] A customer orders 5 ka'ak; on completion, inventory goes from 20 → 15,
      confirmed in the dashboard.
- [ ] Two completions racing for the last unit: exactly one succeeds; the level
      never goes negative (DB-guarded UPDATE + CHECK constraint).
- [ ] A low-stock alert/badge appears in the dashboard when a product crosses its
      threshold.
- [ ] Manual inventory CRUD works from the dashboard (levels, thresholds, suppliers),
      tenant-scoped.

**The HIL loop:**
- [ ] The InventoryAgent drafts a PO when stock is low; it does **NOT** auto-send
      (status `draft`, `dispatched_at` null).
- [ ] The PO appears in the owner's Approvals inbox. Abu Khaled can approve or reject
      (reject requires a reason).
- [ ] On approval, a **signed approval token** is minted and the supplier webhook
      fires (MailHog/log in dev) as a background task — the approve call does not
      hang on a slow supplier.
- [ ] **No code path dispatches a PO without a valid signed token** — a test proves
      an absent/forged/mismatched token is refused (constitution V).
- [ ] On dispatch failure, it retries with backoff; after the budget the PO becomes
      `dispatch_failed` and lands in the manual queue with the reason; "mark sent"
      closes it.
- [ ] The audit log shows every PO action (drafted, approved, rejected, sent,
      dispatch_failed, sent_manually) with who, when, and why.

**Cross-cutting (the Wall + forbidden patterns):**
- [ ] Every Phase 4 table carries a non-nullable indexed `tenant_id`; every new repo
      method filters by it; inventory/PO JOINs are scoped on both sides.
- [ ] A test proves tenant A cannot read/deduct/approve tenant B's inventory or POs.
- [ ] `grep -rn "os.getenv\|print(\|import requests" backend/app/` returns nothing;
      `supplier_dispatch.py` uses `httpx`; provider SDK only under `app/agents/llm/`.
- [ ] CI is green: backend (migrations + full pytest, LLM mocked, webhook stubbed)
      AND the frontend job (lint, typecheck, build).

**Demoable end-to-end:**
- [ ] Owner logs in → completes a real order → stock deducts → a reorder PO is
      drafted → owner approves in the inbox → it dispatches (MailHog) → flips to
      `sent` — all RTL, Lebanese Arabic, on a 360px screen.

## Phase 4 — Defend-it Preparation

Practice answering these out loud (these become `docs/PHASE_4_DEFEND_IT.md`):

1. What happens if two orders for the last unit of stock arrive at the same time?
   Show me the exact query that makes the oversell impossible, and the schema
   constraint that backs it.
2. Show me where the HIL gate lives. What prevents a PO from being sent before
   approval — and why is checking `status == "approved"` *not* the gate?
3. Walk me through what the signed approval token contains and how `ActionGate`
   verifies it. What stops me from replaying one PO's token to dispatch a different
   PO?
4. What does the agent do when the supplier webhook fails? Where does a permanently
   failed PO end up, and how does the owner act on it?
5. Why does dispatch fire as a background task after commit instead of inline in the
   approve handler? What breaks if you do it inline?
6. Why is inventory a separate table that references `products` instead of a new
   inventory-products catalog? (Constitution + ROADMAP: one catalog table.)
7. Deduction happens on completion, not confirmation. Why? What happens to stock if
   a confirmed order is never completed?
8. `forecast_demand` returns a number today with no ML. Why is that allowed here,
   and what replaces it in Phase 6 — without changing any caller?
9. How would you handle approval fatigue — what if Abu Khaled has 50 pending
   approvals? (Prose answer; note what Phase 4 does and doesn't build.)
10. Prove the founder/Wall still holds for inventory and POs: show the line that
    keeps tenant A's owner from approving tenant B's purchase order.

If you can't answer any of these without looking, the phase is not done.

## Ready for Phase 5?

You are ready when:
- Every checkbox above is checked.
- All 10 defend-it questions can be answered fluently, out loud, without notes.
- `cd backend && uv run pytest tests` is green (incl. the deduction + HIL suites and
  the no-send-without-token test); the frontend CI job is green.
- A live demo runs end-to-end: a real order completes → stock deducts → a reorder PO
  is drafted → the owner approves → it dispatches to MailHog and flips to `sent` —
  RTL, Lebanese Arabic, on a 360px screen.

Phase 5 is the OCR Pipeline — Abu Khaled photographs a paper supplier bill and Modir
extracts structured data, which (now that Phase 4's inventory + approval loop exists)
flows back into stock through the SAME HIL approval pattern built here, and the
`knowledge_base_docs` rows pending since Phase 3 finally get embedded. Do not start it
until the inventory loop is solid and demoable end-to-end.
```
