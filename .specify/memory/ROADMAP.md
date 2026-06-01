# Modir — Phased Build Roadmap

> A solo developer's path from empty repo to production-grade SaaS.
> No deadlines. Quality over speed. Defend every line.

This document is your north star. Read the current phase before starting work each day.
When something here conflicts with the constitution, the constitution wins.

---

## How to Use This Document

Each phase has:
- **Goal** — one sentence on what you're building
- **Why this phase exists here** — the architectural reason for the order
- **What you build** — concrete deliverables
- **Skills needed** — which SKILL.md files to write before this phase
- **Definition of done** — the checklist that lets you move on
- **Common pitfalls** — what the bootcamp reviews caught people on
- **Defend-it questions** — what you must be able to answer about this phase

Do not skip ahead. Each phase produces something that works end-to-end at its
own level. By Phase 2 you have a working order flow. By Phase 4 you have a
running shop. Each phase is independently demoable.

---

## Core Concept — How Modir Identifies Who's Talking

Before any phase, internalize this. Every WhatsApp message Modir receives
carries two phone numbers:

- **`to`** = the destination (which business's WhatsApp Business number received it)
- **`from`** = the sender

Modir answers two questions on every message:

1. **Which business?** → look up `to` in the `tenants.whatsapp_number` column
2. **What role?** → look up `from` in `tenant_owners` for that tenant
   - Found → role = `owner` (route to supervisor with all 5 agents)
   - Not found → role = `customer` (route to OrderAgent only)

This means:
- Each business has its own WhatsApp Business number (registered at signup)
- The owner's phone(s) are registered separately in `tenant_owners`
- Customers are auto-created on first message, scoped to that tenant
- The same phone messaging two different Modir tenants is two different identities

**Two distinct tables to keep straight:**

| Table | What it represents | Used for |
|-------|-------------------|----------|
| `users` | Dashboard accounts (email + password) | Logging into the React app |
| `tenant_owners` | Phone numbers authorized to talk to Modir as owner | WhatsApp owner-mode routing |

Usually the same person has both. Sometimes not (e.g. an accountant with
dashboard access but no WhatsApp authority).

**Tool allowlists are role-specific.** A customer cannot invoke "show today's
revenue" — that tool simply isn't on the customer's allowlist. Enforced in
code, not in prompts.

The whole identity layer ships in Phase 1. Phase 2 onward uses it via the
`resolve_message_identity` dependency.

---

## Phase 0 — Foundation & Setup

**Goal:** A clean repo where `docker compose up` brings up an empty but
working FastAPI service with structured logging, secrets in Vault, and a
migration that runs once.

**Why this phase exists here:** Every shortcut you take in setup compounds.
If `os.getenv` shows up in module 1, it shows up in module 50. Set the bar
high on day one and the rest of the project enforces itself.

### What you build
- Repo skeleton with the layer structure from the constitution (`api/`, `services/`, `repositories/`, `domain/`, `infra/`, `db/`, `agents/`, `ml/`, `ocr/`)
- `docker-compose.yml` with: `api`, `migrate`, `db` (postgres + pgvector), `redis`, `minio`, `vault` (dev mode)
- `Settings` class via pydantic-settings — every config value typed and validated
- Vault integration: secrets resolve at startup; `grep -ri 'api_key' app/` returns zero matches
- `structlog` configured for JSON logging — written to file, not just stdout
- Alembic baseline migration — `migrate` container runs and exits before `api` starts
- `FastAPI` app with a `/health` endpoint and `lifespan` handler skeleton
- `pyproject.toml` managed by `uv` — pinned versions, no `pip` anywhere
- `.gitignore`, `.env.example`, `pre-commit` config with `ruff` + `black`
- GitHub Actions CI that runs lint and a single import-test on every push

### Skills needed first
- `git-workflow/SKILL.md` — branch naming, commit format
- `python-backend/SKILL.md` — async, DI, settings, layering

### Definition of done
- [ ] `git clone && cp .env.example .env && docker compose up` works from a fresh machine
- [ ] `curl localhost:8000/health` returns 200
- [ ] Killing the api container and bringing it back up: data and logs survive
- [ ] `grep -ri 'os.getenv\|print(' app/` returns zero results
- [ ] CI is green on the first commit
- [ ] You can explain every line of `docker-compose.yml` and every directive in every `Dockerfile`

### Common pitfalls
- Starting with a notebook and "we'll containerize later." You won't. Start with the container.
- Hardcoding `localhost:5432` instead of `db:5432` — services talk by name on the Docker network.
- Forgetting the `migrate` service. The `api` container should refuse to start if migrations haven't run.
- Putting passwords in `.env` instead of Vault. The reviewer will `grep` for them.

### Defend-it questions
- Why is `migrate` a separate service instead of running in the api container at startup?
- Walk me through what happens when you run `docker compose up` on a fresh clone.
- Where does the Gemini API key actually live? Show me the code that reads it.
- What does your `lifespan` handler do today, and what will it do by the end of the project?

---

## Phase 1 — The Wall (Multi-Tenancy + Identity)

**Goal:** Two businesses can sign up. Each has its own data. Business A can
never see Business B's data — and you have tests proving it. Modir can also
tell, from any incoming message, **which business it belongs to and whether
the sender is the owner or a customer**.

**Why this phase exists here:** Multi-tenancy is not a feature you add later.
Every model, every repository method, every Redis key has to know about
`tenant_id`. The role-detection logic (owner vs customer) is part of the same
identity layer — building Phase 2 without it means rewriting the message
handler the moment Abu Khaled wants to chat with Modir over WhatsApp.

### The Identity Model (read this before you build)

Modir has to answer two questions on every incoming WhatsApp message:

1. **Which business?** — answered by the `to` number (the destination)
2. **Who's writing?** — answered by the `from` number (the sender), looked up
   against the tenant's registered owner phones

This means Phase 1 ships with **ten tables**, all foundational. Nothing else
can be built until these exist.

```
── Identity & Access ──────────────────────────────────────────────────────────

tenants            — the business itself
                     (id, name, whatsapp_number, plan_tier, is_active, created_at)

tenant_owners      — phone numbers authorized as owner over WhatsApp
                     (tenant_id, phone_number, name, verified_at)

users              — dashboard accounts (email + password, JWT sessions)
                     (id, tenant_id, email, hashed_password, role)

customers          — auto-created on first message from an unknown number
                     (id, tenant_id, phone_number, display_name, first_seen_at)

audit_log          — every auth event, privilege change, owner-phone addition
                     (id, tenant_id, actor_id, action, target, created_at)

── Business Profile / Knowledge Base ──────────────────────────────────────────

business_profile   — the shop's public identity
                     (tenant_id PK, business_name, description, location,
                      delivery_radius_km, accepts_delivery, accepts_pickup,
                      logo_url, created_at, updated_at)

products           — the full catalog (reused in Phase 4 inventory, Phase 5 OCR,
                     Phase 6 ML forecasting — the same table, not a new one)
                     (id, tenant_id, name_ar, name_en, description_ar,
                      price_lbp, price_usd, unit, category, is_available,
                      image_url, created_at, updated_at)

operating_hours    — when the shop is open, per day
                     (id, tenant_id, day_of_week, open_time, close_time,
                      is_closed, note_ar)
                     note_ar handles exceptions: "مغلق خلال رمضان بعد الإفطار"

business_policies  — key/value store for shop rules
                     (id, tenant_id, key, value)
                     examples: min_order_lbp, delivery_fee_lbp,
                               payment_methods, delivery_zones

knowledge_base_docs — tracks what is embedded in pgvector and its sync status
                     (id, tenant_id, source_type, source_id, content_hash,
                      embedded_at, embedding_status)
                     source_type: "product" | "policy" | "faq" | "operating_hours"
                     This table tells the system whether the vector store is in
                     sync with the profile. If a product price changes, the
                     corresponding embedding is marked stale and re-queued.
```

A `user` (Abu Khaled's dashboard login) and a `tenant_owner` (his WhatsApp
phone) are related but distinct. Usually the same person — but separating them
gives flexibility (the bakery's accountant has dashboard access but no
WhatsApp authority).

**Why the business profile tables belong in Phase 1:** The `products` table is
referenced by Phase 2 (order parsing), Phase 4 (inventory), Phase 5 (OCR
mapping), and Phase 6 (ML forecasting). It cannot be added mid-project without
rewriting foreign keys across four phases. Define it once, here, and every
subsequent phase uses it.

### What you build
- All ten models above — every table defined, migrated, and repository-wrapped before any feature is built
- Tenant signup flow: registers business name, WhatsApp number, at least one owner phone, and creates a blank `business_profile` row
- JWT authentication for dashboard access: sign-up, login, password hashing (`fastapi-users` is fine)
- An **identity resolver** module that takes a webhook payload and returns:
  - `tenant: Tenant` (resolved from destination number — 404 if unknown)
  - `role: Literal["owner", "customer"]` (resolved from sender phone)
  - `actor: TenantOwner | Customer` (the loaded record)
- `get_current_tenant`, `get_current_user`, and `resolve_message_identity` — the only ways to know who's asking
- Repository base class that requires `tenant_id` on every method. No exceptions.
- CRUD endpoints for the business profile: `PUT /profile`, `POST /products`, `PUT /products/{id}`, `DELETE /products/{id}`, `PUT /operating-hours`, `PUT /policies`
- A `knowledge_base_docs` tracking entry is created/updated every time a product, policy, or hours record changes — embedding status starts as `pending`
- A test tenant fixture that creates two tenants with separate WhatsApp numbers, owner phones, and product catalogs
- Cross-tenant tests proving Tenant A can never reach Tenant B's data — including products and profile data
- Owner-customer test: same sender phone, different tenant → different roles correctly resolved
- Audit log captures every authentication event, privilege change, and owner-phone addition

### Skills needed
- `security-spec/SKILL.md` — the 74 agentic rules, especially Sections 2 and 3 on identity and architecture

### Definition of done
- [ ] Two test tenants exist with different WhatsApp numbers, owner phones, and product catalogs. A query in Tenant A's session returns zero rows from Tenant B's data — including products, profile, and policies.
- [ ] Every SQLAlchemy model has `tenant_id` as a non-nullable indexed column
- [ ] Every repository method takes `tenant_id` as a required parameter
- [ ] The identity resolver correctly identifies: known owner phone → owner; unknown phone → customer (auto-created); message to unknown destination → 404
- [ ] Same sender phone messaging two different tenants resolves to different identities correctly
- [ ] Business profile CRUD endpoints work: create a product, update it, delete it — all scoped to the right tenant
- [ ] When a product is updated, a `knowledge_base_docs` row is created or updated with `embedding_status = "pending"`
- [ ] JWT expiry is short (15 min); refresh token mechanism is documented even if not yet implemented
- [ ] A failing test exists that *tries* to bypass tenant scoping and gets blocked
- [ ] Adding a new owner phone goes through a verification flow (the owner approves from an already-verified channel)
- [ ] Audit log captures who logged in, when, from what tenant, and every owner-phone and product change

### Common pitfalls
- Putting `tenant_id` in the JWT and trusting it. The token says the tenant; the database query must still filter.
- Forgetting tenant scoping on `JOIN` queries — Tenant A's order joining to Tenant B's product is a leak.
- Letting the service layer hit raw SQL to "just this once" bypass the repository. It always becomes "every time."
- Storing the JWT secret in `.env` instead of Vault.
- Treating `tenant_owners` as the same thing as `users`. They're related but separate — confusing them creates a security mess later.
- Letting anyone add an owner phone without verification. Once a phone is in `tenant_owners`, it gets owner-level agent access — that's a privilege escalation if unverified.
- Creating a separate `products` table per phase (e.g. an "inventory_products" table in Phase 4). There is ONE `products` table defined here. Every phase references it.
- Skipping `knowledge_base_docs`. Without it you have no way to know if the vector store is stale when a product price changes.

### Defend-it questions
- Where exactly is tenant isolation enforced? Show me the line of code.
- A WhatsApp message arrives. Walk me through resolving its tenant AND its role, line by line.
- What happens if a malicious user changes the `tenant_id` in their JWT payload?
- Abu Khaled adds his wife's phone as a co-owner. What's the verification flow?
- The same phone number messages two different Modir tenants — show me why they get treated as two separate identities.
- Abu Khaled updates a product price. Walk me through what happens in the database AND the knowledge base tracking table.
- What does `Authorization: Bearer ...` actually carry? What's inside the JWT?
- When the token expires, what does the frontend do?
- What if someone signs up with a WhatsApp number that's already registered to another tenant?
- Show me the `products` table. Why does Phase 4 not create its own inventory table?

---

## Phase 2 — Customer Order Flow (The Heartbeat)

**Goal:** A customer messages a business's WhatsApp number in Lebanese
Arabic. Modir uses the Phase 1 identity resolver to know it's a customer
(not the owner), runs the OrderAgent, saves the order, and replies. The
owner sees the order in the dashboard.

**Why this phase exists here:** This is the irreducible core of Modir. If a
customer can't place an order, nothing else matters. Build the full path
end-to-end with one agent. Resist scope creep — no inventory deduction,
no ML, no dashboard polish. Just: message in, identity resolved, order out.

### What you build

The **message dispatcher** sits at the entry of every webhook. It uses the
identity resolver from Phase 1 to route the message based on role. For
Phase 2 only the customer path is implemented; the owner path returns a
placeholder "owner chat coming soon" reply. Phase 7 fills in the supervisor.

```python
# app/api/webhooks.py — simplified
async def whatsapp_webhook(payload: WhatsAppWebhookPayload, identity = Depends(resolve_message_identity)):
    if identity.role == "customer":
        return await order_agent.handle(payload.text, identity)
    else:  # owner — placeholder until Phase 7
        return await reply_placeholder(identity)
```

What ships in Phase 2:
- WhatsApp webhook receiver (or Telegram bot to start — easier in development)
- Pydantic schema for incoming messages
- Message dispatcher that uses `resolve_message_identity` (Phase 1 dependency)
- A single LangGraph agent: the `OrderAgent` with **three tools**
  - `get_products` — reads the tenant's product catalog to validate what can be ordered, check availability, and get prices. This is called FIRST before parsing. The agent cannot accept an order for something not in the catalog.
  - `parse_order` — extracts items, quantities, pickup/delivery, and time from Lebanese Arabic text, validated against the catalog returned by `get_products`
  - `confirm_order` — writes the validated order to the database, scoped to the right tenant and customer
- Lebanese Arabic prompt templates (separate file, not inline strings)
- `Order`, `OrderItem`, `Product` models — all with `tenant_id`
- `Customer` records get auto-created by the Phase 1 resolver on first contact; Phase 2 enriches them with name extraction from messages
- Pydantic validation on every tool input — bad LLM output triggers a retry, not a crash
- Confirmation message back to the customer in Lebanese Arabic
- Owner placeholder reply when a registered owner phone messages (a polite "this feature is coming in Phase 7" response — proves the routing works)
- Structured logging of every order: tenant_id, customer_id, what, when, which tools fired
- LangSmith tracing wired in from day one of this phase

### Skills needed
- `agent-patterns/SKILL.md` — LangGraph supervisor, tool validation, HIL
- `python-backend/SKILL.md` — already created, but Phase 2 stress-tests async + DI

### Definition of done
- [ ] Send "مرحبا بدي ٥ كعكات بكرا الصبح" from an unknown number to the bakery's WhatsApp. Order lands in the database for that tenant with the right items, quantity, and pickup time.
- [ ] Send an order for a product that does NOT exist in the catalog ("بدي بيتزا"). The agent replies politely that it's not available — it does not hallucinate a confirmation.
- [ ] Send an order for a product marked `is_available = false`. The agent replies that it's currently unavailable.
- [ ] Send the same message from a registered owner phone. The dispatcher routes to the owner-placeholder, NOT the OrderAgent — proves Phase 1's role detection works in production.
- [ ] The customer gets a confirmation reply in Lebanese Arabic with the total price in LBP.
- [ ] The customer record was auto-created on first message and reused on the second.
- [ ] If the LLM returns a malformed tool argument, the agent retries — the run does not crash.
- [ ] Send a message to a WhatsApp number that isn't registered with any tenant → returns 404 cleanly.
- [ ] LangSmith shows the full trace: webhook → identity resolved → get_products → parse_order → confirm_order → reply.
- [ ] Every tool call appears in the structured log with `tenant_id`, `role`, and token usage.

### Common pitfalls
- Calling the LLM SDK synchronously inside an async route. The whole server freezes.
- Skipping Pydantic validation on tool inputs because "the LLM usually gets it right." It doesn't.
- Storing prompts as Python string literals scattered through the codebase. Move them to `prompts/` files immediately.
- Using a powerful model for the parsing step. This is Tier 1 work — Haiku-class is enough and cheaper.
- Trusting the customer's name from the WhatsApp display name without re-validating each time it changes. Names update; track a history.
- Letting the OrderAgent confirm an order without calling `get_products` first. The catalog is the truth — the agent cannot invent products.
- Letting the OrderAgent's tools be reachable from the owner path "just in case." Tool allowlists are role-specific.

### Defend-it questions
- Walk me through the path of a customer message from webhook to database row, including where the identity is resolved.
- A registered owner sends a message that looks like a customer order ("بدي 5 كعكات"). What does the system do, and why?
- What model does the parse step use? Why not Gemini Pro?
- What happens when the LLM is rate-limited mid-conversation?
- Show me where the prompt for `parse_order` lives. Why is it in its own file?
- A new customer messages for the first time. Walk me through what records get created.

---

## Phase 3 — Owner Dashboard (See What's Happening)

**Goal:** Abu Khaled logs into the dashboard and does two things: first he
sets up his shop (products, hours, policies) so the system knows what it's
selling; then he sees his live orders. The setup wizard must come before the
order feed — without products in the catalog, Phase 2 has nothing to validate
orders against.

**Why this phase exists here:** You now have data flowing in, and Abu Khaled
needs both to configure his shop and to see what's happening. The setup wizard
unblocks a real test of Phase 2 (place a real order against a real catalog).
Resist the urge to add charts before the list view and setup flow work on a
Lebanese owner's phone over a slow connection.

### What you build

**Part A — Business Profile Setup Wizard (ships first)**
- Onboarding flow triggered on first login: "مرحبا بك في مودير — خلّينا نضبط محلك"
- Step 1: Business details — name, description, location, delivery radius, payment methods
- Step 2: Products — add name (Arabic + English), price in LBP, unit, category, availability toggle
- Step 3: Operating hours — per day, open/close times, closed toggle, Ramadan hours note
- Step 4: Policies — minimum order, delivery fee, delivery zones
- On wizard completion: a `knowledge_base_docs` embedding job is queued for every product, policy, and hours record (embedding happens in Phase 5 — here we just mark them as `pending`)
- A "Setup incomplete" banner in the dashboard if the wizard was never finished

**Part B — Live Operations View**
- React + Vite frontend, mobile-first, RTL layout for Arabic
- Login flow that hits your existing auth endpoints
- `/orders/today` endpoint — paginated, filtered by current tenant
- `/customers` endpoint — list with last order, total spent, last seen
- Real-time order feed (polling every 5s; WebSockets later if needed)
- Lebanese Arabic UI labels with English fallback
- Dual currency display (LBP and USD)
- A `whoami` panel showing the logged-in business name and plan

### Skills needed
- `modir-frontend/SKILL.md` — RTL rules, Arabic font stack, mobile-first breakpoints, LBP/USD formatting

### Definition of done
- [ ] Abu Khaled signs in for the first time. The setup wizard launches automatically.
- [ ] He adds 5 products with Arabic names, LBP prices, and availability toggles. They appear in the catalog API.
- [ ] He sets operating hours including a "closed on Sundays" rule and a Ramadan note.
- [ ] After wizard completion, `knowledge_base_docs` has one `pending` row per product, policy, and hours record.
- [ ] A customer sends an order for one of those products — it goes through Phase 2's OrderAgent correctly.
- [ ] Abu Khaled sees the order appear in the live dashboard within 10 seconds.
- [ ] The customer list shows all customers for this tenant — and only this tenant.
- [ ] Money displays in both LBP and USD with proper Arabic number formatting.
- [ ] The UI works on a 360px wide screen (typical Lebanese smartphone).
- [ ] You can deploy the frontend separately — it has its own Dockerfile and its own dependencies.

### Common pitfalls
- Forgetting RTL — Tailwind's default is LTR. Set `dir="rtl"` on the root and use `ms-*`/`me-*` (logical) instead of `ml-*`/`mr-*` (physical).
- Hardcoding the API URL in the React app. Use environment variables with sensible fallbacks.
- CORS misconfiguration. The owner's browser and your API are on different origins; the headers have to be right.
- Loading all orders at once instead of paginating. Abu Khaled will have thousands within months.

### Defend-it questions
- Why is the frontend in a separate container with its own dependencies?
- Why polling instead of WebSockets for the order feed? When would you switch?
- How does CORS work in your setup? Show me the FastAPI middleware.
- What happens if Abu Khaled refreshes the page mid-session?

---

## Phase 4 — Inventory & The First HIL Loop

**Goal:** When a customer orders 5 ka'ak, Modir deducts 5 from inventory.
When stock runs low, Modir drafts a purchase order — but doesn't send it
until Abu Khaled approves.

**Why this phase exists here:** Inventory is the first place Modir does
something *with consequences*. A wrong purchase order costs money. This is
where human-in-the-loop becomes non-negotiable. Build the HIL pattern here
once and reuse it for the rest of the project.

### What you build
- `Product` and `Inventory` models — tenant-scoped, of course
- Manual inventory CRUD in the dashboard (Abu Khaled adds his products)
- Order completion triggers an inventory deduction (atomic transaction)
- Low-stock threshold per product (configurable in dashboard)
- The `InventoryAgent` with three tools:
  - `check_stock` — read current levels
  - `forecast_demand` — placeholder for now, real ML in Phase 6
  - `draft_purchase_order` — proposes a reorder, doesn't send
- HIL approval flow: drafted PO appears in an "Approvals" inbox in the dashboard
- Abu Khaled approves or rejects with a reason — both logged in the audit log
- Approved POs trigger a webhook (to a supplier email or Slack channel)

### Skills needed
- Pattern: the HIL flow from Week 5 (Drift Triage Co-Pilot) — agent proposes, human approves through dashboard, action dispatches through queue

### Definition of done
- [ ] Customer orders 5 ka'ak → inventory goes from 20 to 15 → confirmed in the dashboard
- [ ] Low-stock alert appears when threshold is crossed
- [ ] InventoryAgent drafts a PO; it does NOT auto-send
- [ ] PO appears in the Approvals inbox. Abu Khaled can approve or reject.
- [ ] On approval, webhook fires. On failure, retries with backoff. After retry budget, queued for manual.
- [ ] Audit log shows every PO action with who, when, and why

### Common pitfalls
- Letting the agent send the PO without approval to "make the demo smoother." This is exactly what destroys trust in production AI.
- Race conditions on inventory deduction. Two concurrent orders for the last unit — what happens? Use database-level constraints.
- Sending the webhook synchronously inside the approval handler. If the supplier email is down, the approval call hangs.
- Forgetting to handle the rejection case. Why did Abu Khaled reject? Capture the reason; it's gold for improving the agent.

### Defend-it questions
- What happens if two orders for the last unit of stock arrive at the same time?
- Show me where the HIL gate lives. What prevents the PO from being sent before approval?
- What does the agent do when the supplier webhook fails?
- How would you handle approval fatigue — what if Abu Khaled has 50 pending approvals?

---

## Phase 5 — OCR Pipeline (Digitizing Paper)

**Goal:** Abu Khaled snaps a photo of a paper supplier bill. Modir extracts
the items, quantities, and amounts into structured data, ready for inventory
and finance updates.

**Why this phase exists here:** Lebanese SMEs run on paper. This is the
unique value proposition. By this phase you have the structure (inventory,
finance models) for the OCR output to flow into. Building OCR earlier would
have meant building it without the receiving structure.

### What you build
- MinIO bucket for bill images, tenant-scoped paths
- Upload endpoint that streams the file directly to MinIO (never to local disk)
- Background worker that polls for new bills and runs OCR
- OCR engine choice: start with Tesseract (free, works offline) or Google Cloud Vision (better Arabic accuracy, costs money). Document the choice in `DECISIONS.md` with cost vs. accuracy reasoning.
- Structured extraction agent: takes OCR text, returns a Pydantic `BillData` model
- Validation: Abu Khaled reviews the extracted data before it commits
- Confidence scores per field — low confidence triggers manual review
- A "bill review" screen in the dashboard with the image side-by-side with the extracted fields

**Knowledge Base embedding (also ships in this phase):**
- The `knowledge_base_docs` rows marked `pending` since Phase 3 finally get processed here
- A worker picks up `pending` rows, embeds the content (product description, policy text, hours), and stores vectors in pgvector — all tenant-scoped
- Every vector chunk carries `tenant_id`, `source_type`, `source_id`, `content_hash`
- When a product is updated after this point, the service layer marks its `knowledge_base_docs` row as `stale`, the worker re-embeds it, and updates the vector
- Two RAG corpora now exist in pgvector:
  - **Business knowledge** — products, policies, FAQs, hours (from the profile)
  - **Historical bills** — past supplier invoices (from OCR)
- The `OrderAgent` gains a `search_knowledge_base` tool: when a customer asks "هل توصلوا لبيروت؟" (do you deliver to Beirut?), it retrieves the answer from the policy embeddings

### Skills needed
- `rag-pipeline/SKILL.md` — for the chunking and retrieval of historical bills
- A new skill: `ocr-pipeline/SKILL.md` — Tesseract config for Arabic, image preprocessing, confidence handling

### Definition of done
- [ ] Upload a real Lebanese supplier bill image. Within 30 seconds, structured data appears for review.
- [ ] Each extracted field has a confidence score visible in the UI.
- [ ] Approved bills update inventory automatically with audit log entries.
- [ ] Rejected bills stay in MinIO with the rejection reason logged.
- [ ] OCR runs in a worker container, never blocking the api container.
- [ ] All `pending` knowledge_base_docs rows from Phase 3 are now `embedded` in pgvector.
- [ ] Update a product price in the dashboard → its `knowledge_base_docs` row becomes `stale` → worker re-embeds it within 60 seconds.
- [ ] A customer asks "بدكن تسليم لسن الفيل؟" (do you deliver to Sin el Fil?) — the OrderAgent retrieves the delivery zone policy from the knowledge base and answers correctly.
- [ ] Historical bills are searchable via RAG (Phase 6 will use this for forecasting context).

### Common pitfalls
- Running OCR synchronously in the upload endpoint. It takes seconds; the request hangs.
- Saving bill images to local disk. They disappear on container restart.
- Trusting OCR output without confidence scores. Arabic OCR makes mistakes that look correct.
- Not preprocessing images (deskew, denoise, contrast). Raw phone photos OCR poorly.

### Defend-it questions
- Why did you choose Tesseract over Cloud Vision (or vice versa)?
- Walk me through what happens from "Abu Khaled uploads a photo" to "inventory updated."
- What's your confidence threshold for auto-approval? How did you pick it?
- Where does the image actually live, and how do you prevent Tenant A from seeing Tenant B's bills?

---

## Phase 6 — The ML Layer

**Goal:** Real trained models predicting demand for next week, identifying
at-risk customers, and flagging anomalies in daily revenue.

**Why this phase exists here:** ML needs data. By now you have months of
real (or seeded) order data, inventory movements, and customer patterns.
You can train on actual Modir data instead of toy datasets. This is also
where the constitution's "compare 3 classifiers, justify your features"
discipline pays off.

### What you build
- Three models, each with its own training pipeline:
  - **Demand forecaster** — per product, daily demand prediction (regression)
  - **Churn classifier** — which customers are at risk (binary)
  - **Revenue anomaly detector** — is today's revenue weird? (unsupervised + threshold)
- `results.csv` tracking every experiment: model, params, metrics, timestamp
- A proper `sklearn.Pipeline` per model — preprocessing inside the pipeline, no leakage
- k-fold cross-validation, hyperparameter tuning on at least one model
- Per-class metrics for the churn model (the imbalance is real — most customers don't churn)
- Lebanese seasonality features: day-of-week, Ramadan flag, summer-mountain flag, holiday flag
- Models saved with joblib, loaded once via lifespan, served through dependency injection
- A `/predictions` endpoint per model for the agents to call
- Golden eval set in CI: 20 hand-curated cases per model that must not regress

### Skills needed
- A new skill: `ml-pipeline/SKILL.md` — scikit-learn Pipeline patterns, k-fold, leakage prevention, joblib saving, lifespan loading

### Definition of done
- [ ] Three trained models, all loaded at startup, all serving predictions
- [ ] `results.csv` shows at least three classifiers per task with CV mean and std
- [ ] Each model has a documented labeling rule and feature justification in `DECISIONS.md`
- [ ] Lebanese seasonality features are in the training data and documented
- [ ] Golden evals in CI — break a model intentionally, watch CI fail
- [ ] A model can be retrained from scratch with a single command

### Common pitfalls
- Leakage. Computing features from data after the prediction date. The most common bug; it inflates accuracy dishonestly.
- Treating Ramadan as a normal month. Lebanese business changes shape; the model needs to know.
- Reporting macro F1 only. Always include per-class for imbalanced problems.
- Training in a notebook and "we'll productionize later." Train in code from the start, version-controlled.
- Loading the joblib model inside the route handler. It belongs in `lifespan`.

### Defend-it questions
- What is your label for the churn model? How did you define "at risk"?
- Walk me through your features. Which is the strongest predictor and how do you know?
- Why this classifier and not the other two you compared?
- Show me where the model is loaded. How many times per process?
- What does the model do when it sees a brand-new product with no history?

---

## Phase 7 — The Full Agent System

**Goal:** All five agents wired into a LangGraph supervisor. The supervisor
routes incoming requests to the right specialist agent. Each agent uses
RAG, ML, and live data tools as needed.

**Why this phase exists here:** Now you have all the building blocks:
ML models, RAG over bills, inventory, customer data. The agents pull from
everything you've built. Building this first would have been building
nothing.

### What you build
- LangGraph supervisor topology — not a chain
- The five specialist agents from the constitution:
  1. Order Agent (already built in Phase 2, integrate it here)
  2. Inventory Agent (already built in Phase 4)
  3. Finance Agent (new — cash flow tracking, anomaly response)
  4. Customer Agent (new — churn re-engagement with HIL)
  5. Advisor Agent (new — strategic synthesis, morning briefing)
- Postgres-backed checkpoints — kill the agent mid-investigation, restart, it resumes
- Multi-provider LLM router with fallback (Gemini Flash → Grok → Claude Haiku)
- Tool allowlist enforced per agent — Customer Agent cannot draft purchase orders
- Cost tracking per agent run, surfaced in the founder admin dashboard

### Skills needed
- A new skill: `langgraph-supervisor/SKILL.md` — supervisor pattern, checkpoints, idempotency, sub-agent topology
- `agent-patterns/SKILL.md` updated with the routing logic

### Definition of done
- [ ] Abu Khaled asks "كيف مبيعاتي اليوم؟" — supervisor routes to Advisor → it pulls revenue, ML anomaly check, and replies in Lebanese Arabic
- [ ] Kill the agent container mid-run. Restart it. It resumes from the last checkpoint.
- [ ] Force Gemini to return an error. The router falls through to Grok. Logged.
- [ ] An LLM tries to call a tool not on the allowlist. It's refused.
- [ ] Cost per query logged. Daily cost per tenant visible in admin.

### Common pitfalls
- Building a chain instead of a supervisor. A chain means every query goes through every agent. A supervisor routes.
- Forgetting idempotency keys on slow tools. Two retries of "send re-engagement message" sends two messages.
- Treating prompts as code but storing them as inline strings. Move them to files, version-control them, treat schema changes as breaking.
- No regression tests on routing logic. The supervisor's routing IS code; test it like code.

### Defend-it questions
- Show me your supervisor's routing logic. What happens when the input is ambiguous?
- How do you guarantee two retries of "send WhatsApp re-engagement" don't send two messages?
- When the agent wakes from a checkpoint and the original tenant no longer exists, what happens?
- What's the cost of a morning briefing? Show me the log line.

---

## Phase 8 — Hardening & Production Readiness

**Goal:** Modir can survive a real day of real traffic. Tests fail CI on
regression. Logs are queryable. Costs are tracked. Failure modes are graceful.

**Why this phase exists here:** Everything works in happy paths. This phase
is where you stress the unhappy paths and make sure none of them take the
system down.

### What you build
- Golden eval sets in CI for every agent (20+ Lebanese Arabic queries each)
- Load tests: 100 concurrent customers messaging different tenants, no cross-tenant leak
- Chaos tests: kill each container in turn, verify graceful recovery
- Structured logging shipped to a real log aggregator (SEQ, Better Stack, or Grafana Loki)
- Cost dashboards per tenant per day, with alerts when thresholds are exceeded
- Backup and restore procedure for Postgres and MinIO, tested at least once
- Rate limiting per tenant on the customer-facing endpoints
- Graceful degradation: if AI is down, manual order entry still works through the dashboard
- README with architecture diagram, deployment guide, and runbook

### Skills needed
- A new skill: `observability/SKILL.md` — structured logging conventions, redaction rules, what to alert on

### Definition of done
- [ ] CI runs all golden evals on every push. A regression fails the build.
- [ ] Kill the Gemini API (block the domain). Modir keeps running; falls back to Grok.
- [ ] Kill Vault. The api container refuses to start with a clear error.
- [ ] 100 concurrent simulated customers across 10 tenants — no cross-tenant data appears anywhere
- [ ] Restore from backup completes in under 15 minutes
- [ ] You can demo "AI down, business still works" and the manual entry flow

### Common pitfalls
- Skipping load tests because "this is just a demo." The moment you put a real customer on it, you wish you had.
- Logging customer phone numbers and full message contents without redaction. PII in logs is a compliance landmine.
- No backup tested before you need one. The first time you restore, it doesn't work.

### Defend-it questions
- What happens when Gemini, Grok, and Claude are all down at the same time?
- Show me the log of a real failure being handled gracefully.
- How do you know cross-tenant isolation holds under load?
- If Postgres dies right now, how long until Modir is back up with no data loss?

---

## Phase 9 — Polish, Demo, and Documentation

**Goal:** Anyone can clone the repo, run one command, and see Modir working.
The README explains every architectural decision. A demo video shows the
full flow end-to-end.

### What you build
- README with architecture diagram, decisions log, runbook
- 5-minute demo video: customer messages WhatsApp → owner sees order → ML predicts demand → Modir drafts a re-engagement message → owner approves → message sends
- DECISIONS.md — every non-obvious choice with the reasoning
- A "for reviewers" section that pre-empts the defend-it questions
- LICENSE, CONTRIBUTING.md, SECURITY.md
- Optional: deploy a demo instance to Railway/Fly.io for live access

### Definition of done
- [ ] A stranger can `git clone && docker compose up` and have Modir running in under 5 minutes
- [ ] You can defend every line of code in your repo, out loud, without notes
- [ ] The demo video shows real Lebanese Arabic flowing end-to-end
- [ ] Every "why did you do X" question has an answer in DECISIONS.md or the constitution

---

## Future Phases (Out of Scope for v1)

Once v1 is solid:
- **Phase 10:** Direct WhatsApp Business API integration (after Meta approval)
- **Phase 11:** POS integrations (Touch Resto, iiko)
- **Phase 12:** Bank API integration for transaction sync
- **Phase 13:** Supplier marketplace inside Modir
- **Phase 14:** Multi-location and franchise support

---

## The Meta-Rule

Every phase produces something that works end-to-end at its own level.
After Phase 2 you have an order flow. After Phase 4 you have a shop.
After Phase 7 you have Modir.

Don't move on until the current phase's "Definition of done" is fully
checked. Don't shortcut "Common pitfalls" because they're tedious — those
are exactly the questions you'll be asked.

Build small. Trace everything. Defend every choice.
