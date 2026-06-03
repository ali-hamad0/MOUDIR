# Phase 3 — Owner Dashboard (See What's Happening) + Founder-Gated Onboarding

> **Hand this file to Claude Code in VS Code with:**
> "Read `.specify/memory/constitution.md`, `.specify/memory/phases/PHASE_1.5_FOUNDER_ONBOARDING.md`, and this file. Implement Phase 3 task by task. Pause for approval after each task before committing."

> 🚪 **Founder-gated onboarding folds in here.** Phase 1.5
> (`PHASE_1.5_FOUNDER_ONBOARDING.md`) is a HARD REQUIREMENT before the first
> real (non-test) shop or any public/beta launch, and its natural home is this
> phase — the dashboard is where the founder gets a real approvals screen. We
> build the **dashboard first** (fastest path to a demoable owner experience
> against the existing self-service `/auth/register`), **then** fold in the
> founder flow (lock down register, founder-admin identity + auth, email infra,
> activation, approvals UI). 1.5's Definition of Done is part of Phase 3's DoD —
> Phase 3 is not done until a real shop cannot exist without founder approval.

---

## Goal

Abu Khaled logs into a React dashboard and does two things, in this order:
1. **Sets up his shop** through a guided wizard (business details → products →
   operating hours → policies) so the catalog Phase 2 validates against actually
   exists.
2. **Sees his live orders** — the orders Phase 2 writes appear in a mobile-first,
   RTL, Lebanese-Arabic feed within ~10 seconds, alongside a customer list, all
   scoped to his tenant and only his tenant.

Then we make Modir invite-only: a prospective owner submits a **signup request**
(no account, cannot log in), the **founder** reviews it in an admin screen and
**approves** (provisioning the tenant + sending a one-time **activation link**)
or **rejects** it. The owner sets their own password via the link and only then
can log in. No plaintext password is ever emailed; the founder-admin identity
sits ABOVE tenants and still cannot cross The Wall.

**Resist scope creep.** No charts/analytics (that's the ML layer + later polish),
no WebSockets (poll every 5s; switch later if needed), no inventory/HIL (Phase 4),
no RAG (Phase 5), no real billing (Phase 1.5 keeps payment out-of-band). Just:
configure the shop → watch orders come in → gate who gets in.

## Prerequisites

- [ ] Phase 2 is complete and merged. All 15 tasks landed; DoD met (`docs/PHASE_2_DEFEND_IT.md`).
- [ ] `uv run pytest backend/tests` is green (73 tests), including the wall-crossing and guardrail tests.
- [ ] `docker compose up` brings up a clean, healthy stack (db, redis, vault, minio, api, migrate); the order flow produces a real order row + Lebanese-Arabic reply end-to-end.
- [ ] These already exist and Phase 3 builds ON them — **do not re-create**:
  - Auth: `POST /auth/register` (self-service, instant token — Phase 3 later locks this down), `POST /auth/login` (tenant resolved by WhatsApp number, then user scoped to tenant). `app/infra/security.py::create_access_token` / `verify_password`. `get_current_user` / `get_current_tenant` in `app/api/deps.py`.
  - Profile CRUD: `PUT /profile`, `POST /products`, `PUT /products/{id}`, `DELETE /products/{id}`, `PUT /operating-hours`, `PUT /policies` (`app/api/profile.py` → `ProfileService`). The wizard is a **frontend over these existing endpoints** — Part A adds almost no new backend.
  - `knowledge_base_docs` is already written `pending` on profile/product/hours changes by `ProfileService` (Phase 1). Phase 3 does NOT re-implement that; the wizard just drives the same endpoints.
  - Orders: `Order`, `OrderItem` models with `tenant_id`; `OrderRepository` (`list_for_customer`), `OrderService`. Customers: `Customer` model, `CustomerRepository.get_by_phone`.
  - `AuditService.record(tenant_id, actor_id, action, target)` — every privileged action is audited.
  - `Settings` (one pydantic-settings class); secrets resolve from Vault via `resolve_secrets` (`secrets_map`). `seed_vault.sh` AND the `vault-seed` service in `docker-compose.yml` are TWO seed paths that must stay in sync.
  - `prompts/` — all user-facing Arabic copy lives in files, never inline. The dashboard's Arabic UI labels are a **frontend i18n concern** (a JSON/TS dictionary in the React app), but any owner-facing copy the *backend* emits (activation email, founder notifications) goes in `prompts/`.

## Architecture decisions (recorded — read before building)

- **Frontend stack:** React 18 + Vite + TypeScript (constitution fixes React 18 + Vite). Its own `frontend/Dockerfile`, its own `package.json`, deployed separately. The API URL comes from a Vite env var (`VITE_API_BASE_URL`) with a sensible dev fallback — never hardcoded (ROADMAP pitfall).
- **Routing/data:** React Router for pages; a thin `fetch`/`axios` client that attaches the JWT and handles 401 → redirect to login. **Polling** (5s interval) for the order feed via a small hook — NOT WebSockets (ROADMAP: "WebSockets later if needed"). Document when you'd switch (high tenant count / high message volume making 5s polling wasteful).
- **State:** keep it minimal — server state via a query hook (TanStack Query is fine) or hand-rolled polling hook; auth token in memory + `localStorage` for refresh-on-reload. No heavy global store in Phase 3.
- **RTL + Arabic:** `dir="rtl"` on `<html>`; Tailwind logical properties (`ms-*`/`me-*`, `ps-*`/`pe-*`, `text-start`/`text-end`) — NEVER physical `ml/mr/pl/pr` (ROADMAP pitfall). Tailwind v3+ honors logical props under `dir="rtl"`.
- **Styling:** Tailwind CSS. Design tokens (below) as CSS variables so light/dark and currency theming stay token-driven, not per-component hex.
- **CORS:** FastAPI `CORSMiddleware` allowing the dashboard origin(s), credentials, the methods the dashboard uses. Origins come from `Settings` (a typed list), default to the Vite dev origin. Never `allow_origins=["*"]` with credentials.
- **Founder-admin identity:** a NEW non-tenant `admins` table (founder lives ABOVE tenants; `users` stays strictly tenant-bound — keeps The Wall simple to reason about, per the 1.5 doc's leaning). `/auth/register` is **kept but guarded founder-only** so the founder can still provision a tenant directly; the public gets `/signup-requests` only.
- **Currency display:** **LBP primary, USD secondary (muted)**, both read straight from the stored `total_lbp` / `total_usd` snapshots (no live FX conversion in the dashboard — Phase 2 already snapshotted both). Arabic-Indic vs Western digits: pick one and apply consistently via a `formatMoney` util; use **tabular figures** so columns don't jitter (ui-ux-pro-max `number-tabular`).
- **Email (Phase 1.5):** `app/infra/email.py` using `httpx.AsyncClient` (constitution: no `import requests`), provider-agnostic. **Dev mode = MailHog / log the email, never send.** SMTP/API creds resolve from **Vault**, never `.env`.

## Design system (grounded via the `ui-ux-pro-max` skill)

Run `python scripts/search.py "<q>" --design-system` yourself to re-derive; the
decisions below are what those searches returned for an operations dashboard for
non-technical SME owners on cheap Android phones (not a youth/marketing site).

- **Pattern / style:** **Data-Dense but scannable operations dashboard** (BI/ops,
  not a flashy landing page). KPI/summary at top, then the live order list. List
  view before any chart (ROADMAP: "resist the urge to add charts before the list
  view and setup flow work"). Mobile-first: it must work at **360px** (and 375px),
  then scale up. Min padding but readable; sticky table/list headers; row
  highlight on new order.
- **Color tokens** (light mode first; SaaS-business palette, WCAG-checked):
  - `--color-primary: #2563EB` (trust blue), `--on-primary: #FFFFFF`
  - `--color-accent: #059669` (emerald — "confirmed / positive"), `--on-accent: #FFFFFF`
  - `--background: #F8FAFC`, `--foreground: #1E293B`, `--card: #FFFFFF`
  - `--muted: #E9EFF8`, `--muted-foreground: #64748B`, `--border: #E2E8F0`
  - `--destructive: #DC2626` (reject / unavailable / error)
  - Status colors for order state (green confirmed / amber pending / red issue),
    always paired with an **icon or text label**, never color alone (a11y `color-not-only`).
- **Typography:** body/UI **Inter** (Latin) + an **Arabic-capable** face — use
  **Cairo** or **Tajawal** (modern Arabic sans with Latin coverage; both on
  Google Fonts, both have weights 400–700) as the primary Arabic UI font, with
  Noto Naskh Arabic as a serif fallback. ⚠️ The skill's default Latin pairing
  (Inter/Calistoga) does **not** include Arabic glyphs — the Arabic font is a
  hard requirement, not optional. `font-display: swap`; preload only the Arabic
  weight used most. Use a numeric/tabular variant for money and quantities.
- **Non-negotiable UX (ui-ux-pro-max Quick Reference):** touch targets ≥44px;
  visible labels (not placeholder-only); inline validation on blur; error below
  the field with a recovery path; loading→success/error on every submit; empty
  states with guidance ("ما في طلبات بعد"); skeletons for >300ms loads; tables
  overflow-x or switch to card layout on mobile; `prefers-reduced-motion`
  respected; focus rings kept; 150–300ms transitions.

---

## New data model (read this before you build)

Phase 3 adds **no new tenant-scoped tables** for the dashboard itself — it reads
Phase 1/2 tables. The new tables come with Phase 1.5 and are deliberately
**NOT tenant-scoped** because they sit ABOVE tenants (a request has no tenant
until approved; the founder is above all tenants):

```
── Above the Wall (NOT tenant-scoped) ──
admins            — the Modir founder / super-admin (separate from tenant `users`)
                    (id, email, hashed_password, is_active, created_at)
                    The ONE identity allowed to act across tenants, and ONLY
                    through dedicated, audited admin endpoints. Normal
                    tenant-scoped repositories never get a "skip scope" mode.

signup_requests   — pending applications; no tenant until approved
                    (id, business_name, owner_phone, owner_email, status,
                     requested_at, reviewed_at, reviewed_by, reject_reason,
                     paid_at?, provisioned_tenant_id?)
                    status: pending | approved | rejected

── Activation (on the tenant `users` row, tenant-scoped) ──
users gains       activation_token, activation_expires_at, activated_at
                  (or a small activation_tokens table — decide in Task 3.16).
                  Token: secrets.token_urlsafe, single-use, time-boxed.
```

> ⚠️ **The Wall still holds.** `admins` and `signup_requests` having no
> `tenant_id` is correct precisely because they are above the tenant boundary —
> this is NOT a hole in the Wall. The founder reads/writes tenant data ONLY
> through explicit, audited admin endpoints; a tenant-scoped repository is never
> handed a founder "bypass". Task 3.20 proves a founder cannot cross-tenant leak.

---

## The Phase 3 shape (what we wire end-to-end)

```
PART A — Dashboard (build first, demoable against existing self-service auth)

  Browser (React+Vite, RTL, Arabic)
     │  VITE_API_BASE_URL, JWT in Authorization header
     ▼
  CORSMiddleware (FastAPI) ── allowed origins from Settings
     │
     ├─ POST /auth/login ───────────────► JWT (existing)
     │
     ├─ Setup Wizard ─ PUT /profile, POST /products, PUT /operating-hours,
     │                 PUT /policies (ALL existing; wizard is frontend only)
     │                 → ProfileService already marks knowledge_base_docs pending
     │
     └─ Live Ops ─ GET /orders/today (NEW, paginated, tenant-scoped from JWT)
                   GET /customers     (NEW, tenant-scoped, last order/total/seen)
                   GET /me            (NEW, whoami: business name + plan)
                        ▲ polled every 5s by the order feed

PART B — Founder-gated onboarding (fold in; HARD REQ before any real shop)

  Public ─ POST /signup-requests ──► pending row only (NO tenant, NO user, NO login)
                                          │
  Founder ─ POST /admin/login ──► founder JWT (admins table, above tenants)
     │
     ├─ GET  /admin/signup-requests          (list, founder-only, audited)
     ├─ POST /admin/signup-requests/{id}/approve
     │        └─► register_tenant(...) [REUSED] + issue activation token
     │            + email one-time activation link (Vault creds, MailHog in dev)
     └─ POST /admin/signup-requests/{id}/reject  (reason, audited)

  Owner ─ GET /activate?token=…  → POST /activate {token, new_password}
                                     → sets password, marks activated_at → can log in

  /auth/register ── now guarded FOUNDER-ONLY (public can no longer self-provision)
```

Everything runs **async**; every tenant-scoped read takes `tenant_id` from the
authenticated JWT, never from the request body (The Wall). Every founder action
is audited.

---

## Phase 3 — Tasks Overview

| Task | What | Branch |
|------|------|--------|
| **— Part A: Dashboard backend —** | | |
| 3.1 | CORS config in Settings + `CORSMiddleware` | `feature/MOD-3-cors` |
| 3.2 | `GET /orders/today` (paginated, tenant-scoped) + order read schemas | `feature/MOD-3-orders-today` |
| 3.3 | `GET /customers` (last order, total spent, last seen) | `feature/MOD-3-customers-list` |
| 3.4 | `GET /me` whoami (business name, plan, setup-complete flag) | `feature/MOD-3-whoami` |
| 3.5 | Backend read-endpoint tests (tenant isolation, pagination) | `feature/MOD-3-read-tests` |
| **— Part A: Frontend —** | | |
| 3.6 | Vite+React+TS scaffold, Tailwind RTL, fonts, tokens, Dockerfile | `feature/MOD-3-frontend-scaffold` |
| 3.7 | API client + auth (login screen, JWT store, 401 redirect, `/me`) | `feature/MOD-3-auth-ui` |
| 3.8 | App shell: RTL layout, nav, whoami panel, i18n dictionary | `feature/MOD-3-app-shell` |
| 3.9 | Setup wizard (4 steps over existing profile endpoints) | `feature/MOD-3-setup-wizard` |
| 3.10 | "Setup incomplete" banner + wizard gating on first login | `feature/MOD-3-setup-banner` |
| 3.11 | Live order feed (polling 5s, dual currency, empty/loading/skeleton) | `feature/MOD-3-order-feed` |
| 3.12 | Customers list view (mobile card / desktop table, dual currency) | `feature/MOD-3-customers-view` |
| **— Part B: Founder-gated onboarding (Phase 1.5) —** | | |
| 3.13 | `signup_requests` model + migration | `feature/MOD-3-signup-requests-model` |
| 3.14 | `admins` model + founder auth (`/admin/login`), separate from `users` | `feature/MOD-3-founder-admin` |
| 3.15 | Public `POST /signup-requests` (creates pending only) | `feature/MOD-3-request-endpoint` |
| 3.16 | Email infra (`app/infra/email.py`) + activation tokens + Vault creds + MailHog | `feature/MOD-3-email-activation` |
| 3.17 | `POST /activate` (owner sets own password) + Arabic copy | `feature/MOD-3-activate-endpoint` |
| 3.18 | Founder admin API: list/approve(provision+email)/reject; lock `/auth/register` founder-only | `feature/MOD-3-approval-api` |
| 3.19 | Founder approvals screen + activation/set-password screens (frontend) | `feature/MOD-3-approvals-ui` |
| 3.20 | Tests: request→approve→activate→login; reject; expired/used token; founder can't cross the Wall; audit every step | `feature/MOD-3-onboarding-tests` |
| **— Close-out —** | | |
| 3.21 | CI: frontend lint/build job; backend onboarding/read tests; forbidden-patterns still clean | `chore/MOD-3-ci` |

Each task is a separate branch and PR. No exceptions. **Pause for approval after each.**

> **Why dashboard before founder-gating (recorded):** the existing self-service
> `/auth/register` is fine for development (ROADMAP "Planned Insertions": "Until
> then, self-service signup is fine for development"). Building the visible
> dashboard first gets a demoable owner experience fastest; 3.18 then locks down
> `/auth/register` so Phase 3 still finishes with the HARD REQUIREMENT met. No
> real (non-test) shop is onboarded until 3.13–3.20 land.

---

## Task 3.1 — CORS Config in Settings + CORSMiddleware

**Branch:** `feature/MOD-3-cors`

The owner's browser and the API are different origins; CORS must be explicit and
typed — origins come from `Settings`, never `"*"` with credentials (a security
hole). Mobile-first means the dev origin is the Vite server.

**Edit `backend/app/infra/settings.py`** — add (non-secret config):
```python
    # Dashboard CORS — the React app runs on a different origin. Typed list,
    # never "*" with credentials. Dev default is the Vite dev server.
    cors_allow_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"]
    )
```
(`pydantic-settings` reads a list from a JSON-encoded env var or comma-split —
confirm the parsing and document the `.env` form in `.env.example`.)

**Edit `backend/app/main.py::create_app()`** — add the middleware (before routers):
```python
    from fastapi.middleware.cors import CORSMiddleware
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
```

**Commit message:**
```
feat(api): typed CORS config for the dashboard origin

CORS origins are a typed Settings list (never "*" with credentials), defaulting
to the Vite dev server. CORSMiddleware allows the methods + Authorization header
the dashboard needs so the owner's browser can reach the API cross-origin.
```

**Verification (Git Bash, live stack):**
- `docker compose restart api`
- A cross-origin preflight returns the right headers:
  `curl -s -i -X OPTIONS localhost:8000/orders/today -H 'Origin: http://localhost:5173' -H 'Access-Control-Request-Method: GET'` → `access-control-allow-origin: http://localhost:5173`
- An origin NOT in the list does not get an allow-origin header back.
- `grep -rn "os.getenv\|print(\|import requests" backend/app/` still clean.

---

## Task 3.2 — `GET /orders/today` (Paginated, Tenant-Scoped)

**Branch:** `feature/MOD-3-orders-today`

The dashboard's heartbeat. Reads the orders Phase 2 writes, scoped to the JWT's
tenant, paginated (ROADMAP pitfall: never load all orders at once). Returns the
fields the feed shows: items, quantities, dual currency totals, fulfillment,
requested-time text, status, created_at, customer display.

**Add a read method to `OrderRepository`** (stays tenant-scoped via
`_require_tenant_scope`): `list_today(tenant_id, *, limit, offset)` ordered by
`created_at DESC`, plus a count for pagination. "Today" = since local midnight
(document the timezone assumption — Beirut). Eager-load `order_items` to avoid
N+1.

**Add read schemas** in `app/api/schemas/orders.py`: `OrderItemRead`,
`OrderRead` (with `total_lbp`, `total_usd`, `status`, `fulfillment_type`,
`requested_time_text`, `created_at`, customer display name), `OrdersPage`
(`items`, `total`, `limit`, `offset`).

**Add the route** in a new `app/api/orders.py` (mounted in `create_app`):
```python
@router.get("/orders/today", response_model=OrdersPage)
async def orders_today(user: CurrentUser, db: Db, limit: int = 50, offset: int = 0) -> OrdersPage:
    ...  # tenant_id = user.tenant_id, NEVER from the request
```

**Commit message:**
```
feat(api): GET /orders/today — paginated, tenant-scoped order feed

Reads the orders Phase 2 writes, scoped to the JWT tenant (never the body),
paginated with limit/offset (no unbounded loads), items eager-loaded to avoid
N+1, dual LBP/USD totals from the stored snapshots. Powers the live feed.
```

**Verification:**
- Two-tenant test data: tenant A's `/orders/today` returns only A's orders, zero of B's.
- Pagination: `limit=1` returns one item and a correct `total`.
- An order created via the Phase 2 flow shows up with the right items/qty/totals and customer name.
- No N+1 (one query for orders + a bounded number for items; assert via query count or eager load).

---

## Task 3.3 — `GET /customers` (Last Order, Total Spent, Last Seen)

**Branch:** `feature/MOD-3-customers-list`

The customer list, scoped to the tenant. Each row: display name, phone
(redacted per the constitution's logging rule when logged, but shown in-UI to
the owner of their own tenant), last order date, total spent (sum of order
totals), last seen / first seen.

**Add** `CustomerRepository.list_with_stats(tenant_id, *, limit, offset)` — a
tenant-scoped aggregate (orders joined to customers **scoped on both sides** —
constitution I: a JOIN that crosses tenants is a Sev-1 leak). Schema
`CustomerRead` + `CustomersPage` in `app/api/schemas/customers.py`. Route in
`app/api/customers.py`.

**Commit message:**
```
feat(api): GET /customers — tenant-scoped list with last order, total, last seen

Aggregates each customer's order stats with the JOIN scoped on both sides
(cross-tenant JOIN is a Sev-1 leak per constitution I). Paginated; powers the
dashboard customer list. tenant_id comes from the JWT, never the body.
```

**Verification:**
- Tenant A's `/customers` returns only A's customers; B's never appear.
- A customer with two orders shows `total_spent` = sum and the latest `last_order_at`.
- A customer with no orders still appears (left join), totals zero/null.
- Pagination works.

---

## Task 3.4 — `GET /me` Whoami (Business Name, Plan, Setup-Complete)

**Branch:** `feature/MOD-3-whoami`

The dashboard's whoami panel and the wizard-gating flag. Returns the logged-in
user's tenant: business name, plan tier, and a `setup_complete` boolean derived
from the profile (e.g. profile filled + ≥1 product) so the frontend knows
whether to launch the wizard / show the "setup incomplete" banner.

**Add** `GET /me` in `app/api/me.py`: read `business_profile` + product count for
`user.tenant_id`; compute `setup_complete`. Schema `MeResponse`
(`business_name`, `plan_tier`, `email`, `setup_complete`, maybe `product_count`).

**Commit message:**
```
feat(api): GET /me whoami — business name, plan, setup-complete flag

Returns the JWT user's tenant identity plus a derived setup_complete flag
(profile filled + at least one product) so the dashboard can launch the wizard
on first login and show the "setup incomplete" banner otherwise. Tenant-scoped.
```

**Verification:**
- A fresh tenant (blank profile, no products) → `setup_complete = false`.
- After the wizard (profile + a product) → `setup_complete = true`.
- Returns the right business name/plan for the JWT's tenant only.

---

## Task 3.5 — Backend Read-Endpoint Tests

**Branch:** `feature/MOD-3-read-tests`

Reuse `two_tenants` / `db_session`. Prove tenant isolation and pagination on the
three new read endpoints before any frontend touches them.

**File: `backend/tests/test_dashboard_reads.py`** — assert:
1. `/orders/today` for A returns only A's orders (B's are invisible) — the Wall.
2. Pagination: `limit`/`offset` slice correctly; `total` is accurate.
3. `/customers` aggregates totals correctly and never shows another tenant's customer.
4. `/me` `setup_complete` flips false→true after profile + product exist.
5. All three reject an unauthenticated request (401) and never accept a `tenant_id` from the body.

**Commit message:**
```
test(api): dashboard read endpoints — tenant isolation + pagination

/orders/today, /customers, /me each return only the JWT tenant's data (the Wall
reaffirmed at the read layer), paginate correctly, and ignore any tenant_id in
the request body. Unauthenticated requests are rejected.
```

**Verification:**
- `cd backend && uv run pytest tests/test_dashboard_reads.py -v` green.
- Temporarily drop the tenant filter on one endpoint → the isolation test FAILS (confirm, then revert).

---

## Task 3.6 — Vite + React + TS Scaffold (Tailwind RTL, Fonts, Tokens, Dockerfile)

**Branch:** `feature/MOD-3-frontend-scaffold`

Stand up the frontend in `frontend/` as its own deployable: `package.json`,
Vite, React 18, TypeScript, Tailwind, its own `Dockerfile` + `.dockerignore`
(ROADMAP DoD: "deploy the frontend separately — its own Dockerfile and
dependencies"). Wire the design system from the skill.

- `dir="rtl"` and `lang="ar"` on `<html>` in `index.html`.
- Tailwind config: enable logical properties usage; define the color tokens as
  CSS variables (light mode) from the design-system section; spacing on the 4/8
  scale; `prefers-reduced-motion` honored.
- Fonts: load **Cairo/Tajawal** (Arabic UI) + **Inter** (Latin/numbers) via
  `@fontsource` or a self-hosted/`<link>` with `font-display: swap`; tabular
  figures for money.
- `VITE_API_BASE_URL` env var with a dev fallback (`http://localhost:8000`); an
  `.env.example` for the frontend.
- A trivial page that renders "مودير" in the Arabic font, RTL, to prove the
  toolchain + font + direction all work at 360px.

**Commit message:**
```
feat(frontend): React+Vite+TS scaffold with RTL, Arabic fonts, design tokens

Own deployable in frontend/ (package.json, Dockerfile, .dockerignore). dir="rtl"
+ Arabic-capable Cairo/Tajawal UI font + Inter for Latin/numbers, Tailwind with
logical properties and CSS-variable color tokens from the design system. API
base URL is a Vite env var with a dev fallback, never hardcoded. Renders at 360px.
```

**Verification:**
- `cd frontend && npm install && npm run dev` serves the page; Arabic renders RTL in the Arabic font (not a Latin fallback box).
- `npm run build` produces a static bundle; `docker build -f frontend/Dockerfile` succeeds (or document the DNS workaround — build on host if WSL firewall drops DNS).
- Page is usable at 360px (no horizontal scroll).

---

## Task 3.7 — API Client + Auth (Login, JWT Store, 401 Redirect)

**Branch:** `feature/MOD-3-auth-ui`

A thin API client that attaches the JWT and centralizes 401 handling, plus the
login screen hitting the existing `POST /auth/login` (WhatsApp number + email +
password). Token in memory + `localStorage` so a refresh keeps the session
(answers the defend-it "what happens if Abu Khaled refreshes mid-session?").

- `apiClient` (fetch/axios) reads `VITE_API_BASE_URL`, adds
  `Authorization: Bearer`, on 401 clears the token and routes to `/login`.
- Login form: labels (not placeholder-only), inline validation on blur,
  loading→error states, Arabic copy, `inputMode`/`type` for the right mobile
  keyboard. The vague "invalid credentials" message is preserved (don't leak
  which field was wrong — mirrors the backend).
- On success: store token, fetch `/me`, route to dashboard (or wizard if
  `setup_complete=false`).

**Commit message:**
```
feat(frontend): API client + login (JWT store, 401 redirect, refresh-safe)

apiClient attaches the JWT from VITE_API_BASE_URL and centralizes 401 → /login.
The login screen hits POST /auth/login with labeled, blur-validated fields and
loading/error states in Arabic; token persists across refresh via localStorage.
```

**Verification:**
- Valid credentials → token stored, lands on dashboard/wizard.
- Wrong password → vague Arabic error, no token stored.
- Hard refresh after login → still authenticated (token rehydrated).
- A 401 from any call → redirected to login, token cleared.

---

## Task 3.8 — App Shell: RTL Layout, Nav, Whoami Panel, i18n Dictionary

**Branch:** `feature/MOD-3-app-shell`

The authenticated shell: RTL layout, mobile-first nav (bottom nav ≤5 items on
mobile / sidebar ≥1024px per ui-ux-pro-max `adaptive-navigation`), a whoami panel
(business name + plan from `/me`), logout (visually separated from normal nav —
`destructive-nav-separation`), and a single i18n dictionary (TS/JSON) so every
UI string is Lebanese Arabic with an English fallback key (no hardcoded strings
scattered in components — mirrors the backend's `prompts/` discipline).

**Commit message:**
```
feat(frontend): RTL app shell — adaptive nav, whoami panel, i18n dictionary

Mobile-first RTL shell: bottom nav on phones, sidebar ≥1024px; whoami panel from
/me; logout separated from primary nav. All copy comes from one Arabic i18n
dictionary (English fallback keys), never inline literals — the frontend mirror
of the backend prompts/ rule.
```

**Verification:**
- Nav adapts: bottom nav at 360px, sidebar at 1024px; active route highlighted.
- Whoami shows the logged-in business name + plan.
- No hardcoded Arabic literals in component JSX (all via the dictionary).

---

## Task 3.9 — Setup Wizard (4 Steps Over Existing Profile Endpoints)

**Branch:** `feature/MOD-3-setup-wizard`

Part A's centerpiece. A 4-step wizard that drives the **existing** profile
endpoints — almost no new backend. Multi-step progress indicator, back nav,
auto-save per step where sensible (`multi-step-progress`, `form-autosave`).

- Step 1 — Business details → `PUT /profile` (name, description, location,
  delivery radius, accepts delivery/pickup, payment methods).
- Step 2 — Products → `POST /products` per item (name_ar + name_en, price_lbp,
  price_usd, unit, category, `is_available` toggle). Show the running catalog.
- Step 3 — Operating hours → `PUT /operating-hours` (per day open/close, closed
  toggle, Ramadan `note_ar`).
- Step 4 — Policies → `PUT /policies` (min_order_lbp, delivery_fee_lbp,
  delivery_zones, payment_methods).
- On completion: `knowledge_base_docs` rows are already marked `pending` by
  `ProfileService` on each write — the wizard does NOT re-implement that; verify
  the rows exist after completion. Welcome copy "مرحبا بك في مودير — خلّينا نضبط محلك".

**Commit message:**
```
feat(frontend): 4-step setup wizard over the existing profile endpoints

Business details → products → operating hours → policies, each step driving the
existing PUT /profile, POST /products, PUT /operating-hours, PUT /policies. Step
progress + back nav + per-step save; dual LBP/USD price inputs; Ramadan hours
note. ProfileService already queues knowledge_base_docs as pending on each write.
```

**Verification (live stack):**
- Walk all 4 steps → profile populated, ≥5 products in `GET /products`, hours incl. a "closed on Sundays" + Ramadan note, policies set.
- `knowledge_base_docs` has one `pending` row per product / policy / hours record (query the DB).
- A customer order for one of those products goes through Phase 2 correctly (the catalog now exists).
- Works at 360px; validation/labels/error states correct.

---

## Task 3.10 — "Setup Incomplete" Banner + Wizard Gating on First Login

**Branch:** `feature/MOD-3-setup-banner`

First login (`setup_complete=false` from `/me`) launches the wizard
automatically; otherwise the dashboard shows a dismissible-but-persistent
"setup incomplete" banner with a CTA back into the wizard (ROADMAP DoD: "Setup
incomplete banner if the wizard was never finished").

**Commit message:**
```
feat(frontend): first-login wizard gating + "setup incomplete" banner

When /me reports setup_complete=false the wizard launches on login; otherwise a
persistent banner with a CTA into the wizard reminds the owner to finish. The
flag is derived server-side (profile + at least one product), not guessed client-side.
```

**Verification:**
- New tenant → wizard launches automatically on first login.
- Incomplete setup → banner present; completing it clears the banner (re-fetch `/me`).

---

## Task 3.11 — Live Order Feed (Polling 5s, Dual Currency, States)

**Branch:** `feature/MOD-3-order-feed`

The "see what's happening" view. Polls `GET /orders/today` every 5s, renders
each order with items/qty, **LBP primary + USD muted secondary**, fulfillment,
requested-time text, status (color + icon/label, never color alone), and time.
New orders appear within ~10s (DoD). Skeleton on first load, empty state ("ما في
طلبات اليوم بعد"), error state with retry. List virtualizes if long.

- A small `usePolling` hook (5s) — document why polling not WebSockets and when
  you'd switch.
- `formatMoney` util: tabular figures, LBP grouping, USD 2-dp, consistent digit
  style. Pull both numbers straight from the order's snapshots.

**Commit message:**
```
feat(frontend): live order feed — 5s polling, dual currency, full states

Polls GET /orders/today every 5s; new orders surface within ~10s. Each order
shows items/qty with LBP primary + USD muted (from the stored snapshots, tabular
figures), status as color+icon+label, and the raw Arabic requested-time text.
Skeleton, empty, and error+retry states included. Polling (not WebSockets) is
documented with the switch criteria.
```

**Verification (live stack):**
- Place an order via the Phase 2 webhook → it appears in the feed within ~10s without a manual refresh.
- LBP shown primary, USD muted; digits/grouping consistent; columns don't jitter.
- Empty tenant → empty state; kill the API → error+retry, not a blank screen.
- Usable at 360px (cards, no horizontal overflow).

---

## Task 3.12 — Customers List View

**Branch:** `feature/MOD-3-customers-view`

Renders `GET /customers`: card layout on mobile, table on desktop
(`overflow-x-auto` / card switch per ui-ux-pro-max `data-table`). Columns: name,
last order, total spent (dual currency), last seen. Sortable on desktop with
`aria-sort`; empty state; pagination. Only this tenant's customers (DoD).

**Commit message:**
```
feat(frontend): customers list — mobile cards / desktop table, dual currency

Renders GET /customers as cards on phones and a sortable table (aria-sort) on
desktop, with total spent in LBP primary + USD muted, last order, and last seen.
Paginated, empty state included, scoped to the logged-in tenant only.
```

**Verification:**
- Shows only the logged-in tenant's customers.
- Totals match `/orders`; sort + pagination work; readable at 360px.

> 🚪 **Part A is now demoable end-to-end:** log in → wizard → real order in the
> feed → customer list. Before starting Part B, confirm with the owner that the
> dashboard is solid. Part B makes Modir invite-only (HARD REQUIREMENT before any
> real shop) and is required for Phase 3 DoD.

---

## Task 3.13 — `signup_requests` Model + Migration

**Branch:** `feature/MOD-3-signup-requests-model`

A NON-tenant-scoped table (a request has no tenant until approved — see the model
section). Fields per the model section; `status` defaults `pending`. Autogenerate
the migration, **review by hand**, round-trip upgrade/downgrade.

**Commit message:**
```
feat(models): signup_requests table (above the Wall, no tenant_id) + migration

A pending application has no tenant until approved, so signup_requests is
deliberately not tenant-scoped — this is above the tenant boundary, not a hole in
the Wall. status pending|approved|rejected; reviewer + reason + provisioned tenant
captured. Migration autogenerated, reviewed, round-trips.
```

**Verification:**
- `from app.db.models import SignupRequest` imports clean.
- `alembic upgrade head` then `downgrade -1` round-trips on a fresh DB.
- The table has no `tenant_id` (correct — document why in the model docstring).

---

## Task 3.14 — `admins` Model + Founder Auth (`/admin/login`)

**Branch:** `feature/MOD-3-founder-admin`

The founder/super-admin identity, separate from tenant `users` (keeps `users`
strictly tenant-bound). `admins` table (id, email, hashed_password, is_active),
a seed/CLI to create the first founder (password hashed, never a literal), a
founder JWT distinct from tenant JWTs (a separate claim/audience so a founder
token can never be mistaken for a tenant user token), `POST /admin/login`, and a
`get_current_admin` dependency that ONLY authorizes founder endpoints.

> ⚠️ `get_current_admin` must NOT be wired into any tenant-scoped repository or
> give a "skip scope" path. The founder reads tenant data only through explicit
> admin endpoints (3.18), each audited. This is the line that keeps The Wall
> intact while introducing an above-tenant identity.

**Commit message:**
```
feat(auth): founder-admin identity + /admin/login, separate from tenant users

admins is a non-tenant table for the Modir founder (users stay strictly
tenant-bound). Founder JWT carries a distinct audience so it can never act as a
tenant user; get_current_admin authorizes only admin endpoints and is never
handed to a tenant-scoped repository. First founder seeded with a hashed password.
```

**Verification:**
- `POST /admin/login` with the seeded founder → a founder JWT; wrong password → 401.
- A founder JWT cannot satisfy `get_current_user` (and vice versa) — distinct audiences.
- `grep` confirms no admin "bypass" is wired into any tenant repository.

---

## Task 3.15 — Public `POST /signup-requests` (Pending Only)

**Branch:** `feature/MOD-3-request-endpoint`

The public entry: business name, owner phone, owner email → a `pending`
`signup_requests` row. Creates **NO** tenant, **NO** user, **NO** login (1.5
DoD). Basic validation + light anti-abuse (dedupe by email/phone; rate-limit
deferred to Phase 8). Audited (action `signup_request.created`) — but note
audit_log is tenant-scoped, so for above-tenant events decide in 3.20 whether to
log to a dedicated admin audit path or to `audit_log` with a null/sentinel
tenant; keep it consistent.

**Commit message:**
```
feat(api): public POST /signup-requests — creates a pending request only

A prospective owner submits business name + phone + email and gets a pending
signup_requests row: no tenant, no user, no login until a founder approves.
Deduped on email/phone; the event is audited. Self-provisioning is gone for the public.
```

**Verification:**
- Posting a request creates a `pending` row and returns 201/202 — and creates no `tenants`, `users`, or token.
- A duplicate email/phone is handled (rejected or idempotent — decide and document).

---

## Task 3.16 — Email Infra + Activation Tokens + Vault Creds + MailHog

**Branch:** `feature/MOD-3-email-activation`

The stack has no mailer. Add `app/infra/email.py` using `httpx.AsyncClient`
(constitution: no `import requests`), provider-agnostic behind a small interface.
**Dev mode logs the email / sends to MailHog — never sends real mail.** SMTP/API
creds resolve from **Vault** (add to `secrets_map` AND both seed paths —
`seed_vault.sh` and the `vault-seed` service). Add a MailHog service to
`docker-compose.yml` for dev. Activation tokens: `secrets.token_urlsafe`,
single-use, time-boxed — columns on `users` (`activation_token`,
`activation_expires_at`, `activated_at`) via a migration (or a small
`activation_tokens` table; pick and document).

**Commit message:**
```
feat(infra): provider-agnostic email (httpx) + activation tokens + Vault creds

app/infra/email.py sends via httpx.AsyncClient behind a small interface; dev mode
routes to MailHog and never sends real mail. Credentials resolve from Vault
(secrets_map + both seed paths). Activation tokens are token_urlsafe, single-use,
and expiring, stored on the users row via migration.
```

**Verification:**
- In dev, "sending" an email writes to MailHog / logs the rendered email (no real send); no `import requests` anywhere.
- Vault resolves the mail creds at startup (`vault.secrets.resolved count` increases); both seed paths updated (`docker compose up -d --force-recreate vault-seed` succeeds).
- A generated activation token is random, single-use, and expires.
- `grep -rn "os.getenv\|print(\|import requests" backend/app/` still clean.

---

## Task 3.17 — `POST /activate` (Owner Sets Own Password)

**Branch:** `feature/MOD-3-activate-endpoint`

The owner clicks the one-time link and sets their own password — no plaintext
password is ever emailed (1.5 decision). `GET /activate?token=…` validates the
token (exists, unused, unexpired) for the set-password screen; `POST /activate`
{token, new_password} sets the hashed password, stamps `activated_at`, burns the
token. A used/expired/invalid token is rejected with a clear Arabic message
(copy in `prompts/`). Until activation the user cannot log in (login already
verifies a password; an un-activated user has no usable one).

**Commit message:**
```
feat(api): POST /activate — owner sets own password via one-time link

GET validates the activation token for the set-password screen; POST sets the
hashed password, stamps activated_at, and burns the single-use token. Used,
expired, or invalid tokens are rejected in Arabic. No plaintext password is ever
emailed; an un-activated account cannot log in.
```

**Verification:**
- Valid token → password set, `activated_at` stamped, token now unusable; the owner can then log in.
- Reusing the same token → rejected. An expired token → rejected.
- Before activation, login fails (no usable password).

---

## Task 3.18 — Founder Admin API: List / Approve / Reject + Lock `/auth/register`

**Branch:** `feature/MOD-3-approval-api`

The founder's control surface (all behind `get_current_admin`, all audited):
- `GET /admin/signup-requests` — list with status filter.
- `POST /admin/signup-requests/{id}/approve` — **reuse `register_tenant`** to
  provision the tenant + first owner + dashboard user (created WITHOUT a usable
  password) + blank profile, generate an activation token, **email the one-time
  activation link** (3.16), mark the request `approved` + `provisioned_tenant_id`.
  Optionally stamp `paid_at` (payment is out-of-band — 1.5).
- `POST /admin/signup-requests/{id}/reject` — set `rejected` + `reject_reason`,
  audited (optionally notify the applicant).
- **Lock down `/auth/register`:** guard it behind `get_current_admin` so only the
  founder can provision a tenant directly; the public path is now
  `/signup-requests` only. (Decision: kept, founder-only — not deleted.)

**Commit message:**
```
feat(admin): founder approve/reject signup requests; lock /auth/register

Founder-only, audited endpoints list/approve/reject signup requests. Approve
reuses register_tenant (user created without a usable password) + issues an
activation token + emails the one-time link; reject records a reason.
/auth/register is now guarded founder-only — the public can no longer self-provision.
```

**Verification:**
- Founder approves a pending request → a tenant + owner + un-activated user exist, the request is `approved` with `provisioned_tenant_id`, and an activation email is "sent" (MailHog/log).
- Founder rejects → `rejected` + reason recorded; no tenant created.
- `POST /auth/register` without a founder JWT → 401/403 (locked); with founder JWT → still works.
- Every approve/reject is in the audit trail.

---

## Task 3.19 — Founder Approvals Screen + Activation/Set-Password Screens

**Branch:** `feature/MOD-3-approvals-ui`

Frontend for Part B:
- **Founder approvals screen** (behind founder login): a list of pending
  signup requests with approve/reject (reject requires a reason), status badges,
  empty state. Reuses the app shell but is a founder-only area, visually distinct.
- **Public request form** (optional in this task or 3.15-adjacent): submit a
  signup request.
- **Activation / set-password screen**: lands from the email link
  (`/activate?token=…`), validates via `GET /activate`, lets the owner set a
  password with confirm + strength hints (`password-toggle`, `error-clarity`),
  then routes to login on success.

**Commit message:**
```
feat(frontend): founder approvals screen + activation/set-password screens

Founder-only approvals list (approve/reject with reason, status badges, empty
state). The activation screen validates the one-time token and lets the owner set
their own password (confirm + show/hide + clear errors), then routes to login.
```

**Verification:**
- Founder logs in, sees pending requests, approves one → owner receives the activation link (MailHog), sets a password, logs in.
- Reject requires a reason; the UI reflects status.
- Activation screen rejects a used/expired token with a clear Arabic message.

---

## Task 3.20 — Onboarding Tests (Happy Path, Reject, Token, Wall, Audit)

**Branch:** `feature/MOD-3-onboarding-tests`

**File: `backend/tests/test_onboarding.py`** — assert:
1. **Happy path:** request → founder approve → activation email issued → owner
   activates (sets password) → owner logs in. No plaintext password ever emailed.
2. **No login before approval/activation:** a pending request cannot log in; an
   approved-but-not-activated user cannot log in.
3. **Reject path:** founder rejects → no tenant; reason recorded.
4. **Token safety:** a used token is rejected; an expired token is rejected.
5. **The Wall holds:** the founder-admin identity **cannot** read/write tenant
   data through normal tenant-scoped repositories — a test proves a founder
   cannot cross-tenant leak (the 1.5 DoD's explicit requirement; the Phase 1 wall
   test re-expressed at the founder boundary).
6. **Audit:** request, approve, reject, activate are each audited.
7. `/auth/register` is founder-only now (public call → 401/403).

**Commit message:**
```
test(onboarding): request→approve→activate→login; reject; token; Wall; audit

Full happy path with no plaintext password emailed; no login before activation;
reject records a reason and provisions nothing; used/expired tokens rejected; the
founder-admin cannot cross The Wall via tenant-scoped repositories; every step is
audited; /auth/register is founder-only.
```

**Verification:**
- `cd backend && uv run pytest tests/test_onboarding.py -v` green.
- The "founder can't cross the Wall" test FAILS if a tenant repo is handed an admin bypass (confirm, then revert).

---

## Task 3.21 — CI: Frontend Build + Backend Tests; Forbidden Patterns Clean

**Branch:** `chore/MOD-3-ci`

**Edit `.github/workflows/ci.yml`:**
- Add a **frontend job**: `npm ci`, lint (ESLint), typecheck (`tsc --noEmit`),
  `npm run build`. Cache `node_modules`. Fails the build on lint/type/build errors.
- The backend job picks up `test_dashboard_reads.py` and `test_onboarding.py`
  under the existing `uv run pytest backend/tests` step (LLM still mocked; live
  smoke test still deselected).
- Reaffirm forbidden-patterns: no `os.getenv` / `print(` / `import requests` in
  `backend/app/`; provider SDK still confined to `app/agents/llm/`; the new
  `email.py` uses `httpx`, not `requests`. The single LangSmith `os.environ`
  write remains the documented exception.

**Commit message:**
```
ci: frontend lint/typecheck/build job + Phase 3 backend tests

Adds a frontend CI job (npm ci, ESLint, tsc --noEmit, vite build) and runs the
new dashboard-read + onboarding suites in the backend job (LLM mocked). Reaffirms
forbidden patterns: no os.getenv/print/import requests; email.py uses httpx; the
provider SDK stays in app/agents/llm/. A regression fails the build.
```

**Verification:**
- Push the branch; CI runs the frontend job (lint+type+build) and the full backend suite — both green.
- Introduce `import requests` in `email.py` → CI fails; revert → green.

---

## Phase 3 — Definition of Done

Run through this before marking Phase 3 complete (mirrors the ROADMAP DoD + 1.5 DoD):

**Dashboard (Part A):**
- [ ] Abu Khaled signs in for the first time → the setup wizard launches automatically.
- [ ] He adds ≥5 products (Arabic names, LBP prices, availability toggles) → they appear in the catalog API.
- [ ] He sets operating hours including a "closed on Sundays" rule and a Ramadan note.
- [ ] After wizard completion, `knowledge_base_docs` has one `pending` row per product, policy, and hours record.
- [ ] A customer order for one of those products goes through Phase 2's OrderAgent correctly.
- [ ] The order appears in the live dashboard within ~10 seconds (5s polling).
- [ ] The customer list shows all customers for this tenant — and only this tenant.
- [ ] Money displays in both LBP (primary) and USD (muted) with consistent Arabic number formatting.
- [ ] The UI works on a 360px-wide screen, RTL, in the Arabic font (no Latin-fallback boxes, no horizontal scroll).
- [ ] The frontend deploys separately — its own Dockerfile and dependencies; CORS lets the browser reach the API.

**Founder-gated onboarding (Part B / Phase 1.5):**
- [ ] A business owner CANNOT log in without a founder-approved, activated account.
- [ ] Public signup creates a `pending` request only — no tenant, no user, no login.
- [ ] The founder can list, approve, and reject requests from the dashboard; each is audit-logged.
- [ ] Approval provisions the tenant (via `register_tenant`) and emails a one-time activation link. No plaintext password is ever sent.
- [ ] The activation link is single-use and expires; a used/expired link is rejected.
- [ ] After activation the owner sets their own password and can log in.
- [ ] The founder-admin identity is separate from tenant `users` and CANNOT read/write tenant data through normal repositories — a test proves a founder cannot cross-tenant leak.
- [ ] Email credentials resolve from Vault; dev mode does not send real email (MailHog/log).
- [ ] `/auth/register` is founder-only; the public path is `/signup-requests`.

**Cross-cutting:**
- [ ] `grep -rn "os.getenv\|print(\|import requests" backend/app/` still returns nothing; provider SDK only under `app/agents/llm/`.
- [ ] CI is green on `main`: backend (migrations + full pytest, LLM mocked) AND the new frontend job (lint, typecheck, build).

## Phase 3 — Defend-it Preparation

Practice answering these out loud (these become `docs/PHASE_3_DEFEND_IT.md`):

1. Why is the frontend in a separate container with its own dependencies?
2. Why polling instead of WebSockets for the order feed? When exactly would you switch, and what's the cost of 5s polling at scale?
3. How does CORS work in your setup? Show me the FastAPI middleware and where the allowed origins come from — why never `"*"` with credentials?
4. What happens if Abu Khaled refreshes the page mid-session? Where does the JWT live and what happens on a 401?
5. Walk me through the setup wizard: which endpoints does each step hit, and where does `knowledge_base_docs` get marked `pending`? (Trick: the wizard re-implements none of that — `ProfileService` already does it.)
6. How is money shown, and why are both LBP and USD read from the order snapshot instead of converted live in the dashboard?
7. Where does the founder identity live, and why a separate `admins` table instead of a `users` row with a null tenant_id?
8. The founder is "above all tenants." Show me the exact line that stops a founder token from reading Tenant B's orders through a normal repository. How do you prove it with a test?
9. Walk me through request → approval → activation → first login. Where is the password set, and why is no password ever emailed?
10. An activation link is clicked twice. What happens the second time, and where is that enforced?
11. RTL: what breaks if you use `ml-*`/`mr-*` instead of `ms-*`/`me-*`? Why does the Arabic font matter beyond aesthetics (what does Inter alone render for Arabic)?

If you can't answer any of these without looking, the phase is not done.

## Ready for Phase 4?

You are ready when:
- Every checkbox above is checked.
- All 11 defend-it questions can be answered fluently, out loud, without notes.
- `uv run pytest backend/tests` is green (incl. dashboard-read + onboarding + the founder-can't-cross-the-Wall tests); the frontend CI job is green.
- A live demo runs end-to-end: founder approves a request → owner activates and logs in → completes the setup wizard → a real customer order lands in the live feed within 10s — all RTL, Lebanese Arabic, on a 360px screen.

Phase 4 is Inventory & the First HIL Loop — it deducts stock on order completion
and introduces the human-in-the-loop approval pattern (the founder approvals
screen built here is the UI seed for the Approvals inbox Phase 4 reuses). Do not
start it until the dashboard and onboarding are solid and demoable end-to-end.
