# Phase 5 — OCR Pipeline (Digitizing Paper) + Knowledge-Base Embedding

> **Hand this file to Claude Code in VS Code with:**
> "Read `.specify/memory/constitution.md`, `.specify/memory/ROADMAP.md` (Phase 5),
> and this file. Implement Phase 5 task by task. Pause for approval after each task
> before committing."

> 📸 **Lebanese SMEs run on paper — this is Modir's unique value.** Abu Khaled snaps
> a photo of a supplier bill; Modir extracts the items, quantities, and amounts into
> structured data he reviews, and on approval that data flows **back into stock
> through the SAME HIL approval primitive built in Phase 4** — not a second gate.
> This phase also finally embeds the `knowledge_base_docs` rows that have sat
> `pending`/`stale` since Phase 1/3, so the OrderAgent can answer policy questions
> from RAG. Two long-deferred pieces (a background **worker** and **pgvector**) land
> here; build them as carefully as the gate was built in Phase 4.

---

## Goal

Abu Khaled uploads a photo of a paper supplier bill from the dashboard. The file
streams **straight to MinIO** (never local disk), tenant-scoped. A **background
worker** (the first worker container in the project) picks it up, runs OCR (Google
Cloud Vision behind a provider-agnostic `OCREngine` seam), and an extraction agent
turns the OCR text into a structured `BillData` with **per-field confidence
scores**. The bill lands in a **bill-review screen** (image side-by-side with the
extracted fields). That review screen **is the Level-2 HIL approval surface**: on
approve, Modir mints an `ActionGate` token for a new `bill.commit` action and a
gated committer applies an inventory **increase** per validated line — reusing the
Phase 4 gate exactly. Rejected bills stay in MinIO with the reason logged.

In the same phase, the **knowledge-base embedding** finally runs: the worker drains
`knowledge_base_docs` rows marked `pending`/`stale`, embeds the content (product
descriptions, policies, hours), and stores tenant-scoped vectors in **pgvector**.
When a product is edited after this point, its tracking row flips to `stale` (the
Phase 1 hook already does this) and the worker re-embeds it. The **OrderAgent gains
a `search_knowledge_base` tool** so a customer asking "بدكن تسليم لسن الفيل؟" gets
the delivery-zone policy answered from RAG. Historical bills become a second,
separate searchable corpus (Phase 6 uses it for forecasting context).

By the end of Phase 5, paper becomes structured stock under a human's eye, and the
two RAG corpora (business knowledge + historical bills) exist in pgvector.

## Resist scope creep

- **No real ML / no forecasting.** Embedding + retrieval is RAG plumbing, not
  prediction. The demand forecaster, churn, anomaly models are **Phase 6** — do not
  import scikit-learn. The historical-bill corpus is *built* here so Phase 6 can
  query it; Phase 6 does the modelling.
- **No supervisor.** The bill-extraction agent is a standalone LangGraph graph
  (mirroring OrderAgent/InventoryAgent) so Phase 7 can drop it under the supervisor
  without a rewrite. Phase 5 does NOT wire a supervisor.
- **No new HIL gate.** The bill commit reuses `ActionGate` + `mint_approval_token`
  from `app/infra/action_gate.py`, adding only a new action string `bill.commit`.
  Do NOT fork the gate; do NOT bend the `PurchaseOrder` model into a "stock-in PO".
- **No second products/inventory table.** OCR lines map to the ONE `products` table
  and adjust the ONE `inventory` table (constitution: one catalog). Unmapped lines
  are surfaced to the human, never auto-create catalog rows silently.
- **No real outbound supplier integration changes.** Phase 5 touches OCR + KB, not
  the Phase 4 supplier dispatch.
- **No re-implementing `knowledge_base_docs` tracking.** The model, repo
  (`mark_pending_or_stale` / `delete_by_source` / `get_by_source`), and the
  profile-service hooks already exist (Phase 1/3). Phase 5 adds the *consumer* (the
  embedding worker + the vector store + retrieval) — not the tracking layer.

## Prerequisites

- [ ] Phase 4 is complete and merged to `main` (through PR #84). DoD met; defend-it
      write-up at `docs/PHASE_4_DEFEND_IT.md`.
- [ ] `cd backend && uv run pytest tests` is green (the full suite incl.
      `test_inventory_deduction.py`, `test_hil_purchase_orders.py`,
      `test_action_gate.py`, and the no-send-without-token test).
- [ ] `docker compose up` brings up a clean, healthy stack (db [pgvector/pg16],
      redis, vault, minio, mailhog, api, migrate, vault-seed) and the order +
      inventory + approval loop runs end-to-end.
- [ ] These already exist and Phase 5 builds ON them — **do not re-create**:
  - **The HIL gate:** `ActionGate.authorize` + `mint_approval_token` +
    `UnauthorizedAction` + `ApprovedAction` (`app/infra/action_gate.py`). The bill
    commit mints a token for a NEW action string `bill.commit` and a gated
    `BillCommitter` (mirroring `SupplierDispatcher`) verifies it before any stock
    change. `DISPATCH_ACTION = "po.dispatch"` stays; add `COMMIT_ACTION =
    "bill.commit"` next to its committer. **One gate, more action strings.**
  - **The approvals dispatch shape:** `app/services/approvals.py`,
    `app/api/approvals.py`, `app/services/purchase_orders.py` — the
    list→action-with-reason→audited→commit→(mint token)→background-task pattern.
    The bill-review approve flow mirrors it: approve commits the review decision,
    THEN mints the token and fires the committer as a background task (a slow/failed
    commit must not hang the approve call).
  - **Inventory model/repo:** `Inventory` / `Supplier` (`app/db/models.py`) and
    `InventoryRepository` (`app/repositories/inventory.py`). Phase 5 adds an atomic
    `increase(tenant_id, product_id, qty)` mirroring the existing guarded `deduct`
    — a single `UPDATE ... SET quantity = quantity + :n` (no CHECK conflict; an
    increase never goes negative). An upsert path is needed for a tracked-but-zero
    or missing inventory row (a bill can be the first time a SKU enters stock).
  - **The agent pattern:** `app/agents/inventory/agent.py` + `tools.py` —
    lifespan-built compiled `StateGraph`, per-call `ToolContext` via the graph
    `config` (never stored on the instance — concurrency-safe), own session per
    call from the injected `async_sessionmaker`. The `BillExtractionAgent` mirrors
    this EXACTLY (`app/agents/ocr/`).
  - **`knowledge_base_docs`:** the `KnowledgeBaseDoc` model, the
    `KnowledgeBaseDocRepository` (`get_by_source`, `mark_pending_or_stale`,
    `delete_by_source`), and the profile-service hooks that already mark rows
    `pending`/`stale` on product/policy/hours writes (`app/services/profile.py`).
    Phase 5 ADDS the embedding worker that drains them + the pgvector store; it does
    NOT touch the tracking writes.
  - **Provider-agnostic infra patterns:** `EmailSender` (`app/infra/email.py`) and
    `SupplierDispatcher` (`app/infra/supplier_dispatch.py`) — both Vault-credentialed,
    dev-safe, mode-selected (`dev` never leaves the machine). The `OCREngine` and the
    embedding client follow this shape: a Protocol + a dev/test stub + the real
    (Vault-keyed) implementation; CI/tests run the stub, the host venv verifies the
    real one (Docker DNS is blocked here — same verify-on-host discipline as the rest
    of the project).
  - **MinIO config + Vault dual-seed:** `Settings.minio_endpoint /
    minio_access_key / minio_secret_key`, resolved from Vault `modir/minio`
    (`secrets_map` in `app/infra/vault.py`) AND seeded by the `vault-seed` service in
    `docker-compose.yml`. The compose service is ALREADY up; Phase 5 adds the MinIO
    **client** (`app/infra/storage.py`) — any new secret (e.g. the GCP Vision key)
    goes in `secrets_map` AND the `vault-seed` entrypoint, kept in sync.
  - **`LLMRouter`/`GeminiRouter`** (`app/agents/llm/router.py`) — the extraction
    agent's language step (structuring OCR text into `BillData`) is LLM work and
    uses the router; OCR itself (pixels→text) is the `OCREngine`, NOT the LLM
    (constitution IV: LLM structures OCR text, it does not read pixels). The
    provider SDK stays confined to `app/agents/llm/`.
  - **`Settings`** (one pydantic-settings class) + `get_settings`. New non-secret
    Phase 5 config (OCR mode, confidence thresholds, embedding model name, worker
    poll interval, bucket name) goes here, typed; the GCP Vision key (if Cloud
    Vision mode) resolves from Vault.
  - **Frontend:** the Phase 3/4 React+Vite+TS app (RTL, Arabic i18n dictionary,
    `apiClient` with JWT + 401 redirect, app shell + adaptive nav, `formatMoney`,
    dual-currency, skeleton/empty/error states, the Phase 4 Approvals inbox shape).
    Phase 5 adds an Upload + Bill-review screen, not a new app.

## Core constraint reminder — The Wall + The Gate + Tenant-scoped storage

Three non-negotiables govern this phase:

1. **The Wall (constitution I):** every new row carries `tenant_id`; every new repo
   method filters by it. **Vector search filters by `tenant_id` BEFORE similarity,
   never after** (constitution I, literal). MinIO object paths are tenant-prefixed
   so tenant A can never fetch tenant B's bill image. The bill→inventory JOIN is
   scoped on both sides. A test *tries* to cross and is blocked.
2. **The Gate (constitution V):** committing an OCR'd bill to stock is a Level-2
   action. The `BillCommitter` refuses to change stock without a valid signed
   `bill.commit` token bound to that bill id + tenant + approver. There is no
   "commit anyway" path — an accidental call without a token is rejected, not
   committed. `status == "approved"` on the bill is the lifecycle marker, NOT the
   gate (same literal reading as Phase 4).
3. **OCR runs in the worker, never the api request (ROADMAP pitfall).** The upload
   endpoint streams to MinIO and returns immediately; OCR + extraction happen in the
   worker. A raw phone photo OCRs poorly, so images are preprocessed (deskew,
   denoise, contrast) before OCR, and low-confidence fields force manual review.

---

## Architecture decisions (recorded — read before building)

These were decided up front (OCR engine + bill→stock path via explicit owner
sign-off) so the tasks stay coherent. When one conflicts with the constitution, the
constitution wins. They also belong in `docs/DECISIONS.md` (Task 5.16).

- **OCR engine: Google Cloud Vision, behind a provider-agnostic `OCREngine` seam.**
  (Owner decision: Cloud Vision for Arabic accuracy.) Cloud Vision gives materially
  better Lebanese-Arabic accuracy on phone photos than Tesseract, raising the share
  of bills that clear the confidence threshold and reducing manual review. To honor
  the constitution's provider-agnostic rule (and the project's dev-safe / verify-on-
  host discipline given blocked Docker DNS), it is built behind an `OCREngine`
  Protocol with three implementations:
  - `StubOCREngine` — deterministic canned text/confidence for **tests + CI**
    (offline, like the mocked LLM in existing suites) and the dev default when no
    GCP key is configured.
  - `CloudVisionOCREngine` — the real engine; GCP credentials resolve from **Vault**
    (`modir/ocr`), calls out via the official client / `httpx` (never `requests`).
    Verified on the **host venv**, not in-container (DNS is blocked here).
  - `TesseractOCREngine` — documented as the offline fallback; implement only if
    time allows, otherwise note it in DECISIONS.md. Mode selected by
    `Settings.ocr_mode` (`stub` | `cloud_vision` | `tesseract`), mirroring
    `mail_mode` / `po_dispatch_mode`. The cost-vs-accuracy reasoning is recorded in
    `docs/DECISIONS.md`.
- **OCR is pixels→text; the LLM only structures the text (constitution IV).** The
  `OCREngine` returns raw text + per-block confidence. The `BillExtractionAgent`'s
  LLM step turns that text into a validated `BillData` (supplier, date, line items
  with name/qty/unit/amount, totals) — language work, Tier-1, Pydantic-validated,
  bad output retries (the established pattern). The LLM never "reads the image".
- **Per-field confidence + a threshold drives review, not auto-commit.** Each
  extracted field carries a confidence (OCR block confidence × extraction
  certainty, documented formula). A bill is NEVER auto-committed to stock in Phase 5
  — **every** bill goes to human review (the bill-review screen is the gate). Low
  confidence is surfaced visually (the field is flagged) so the human knows where to
  look. `settings.ocr_confidence_review_threshold` marks fields needing attention;
  it is a UI signal, not an auto-approve switch. (Auto-commit of high-confidence
  bills is explicitly OUT of Phase 5 — note it as future work.)
- **The bill review screen IS the HIL approval (owner decision).** No "stock-in PO"
  record, no overloading `PurchaseOrder`. A `SupplierBill` row holds the bill
  lifecycle (`uploaded → ocr_processing → extracted → committed / rejected /
  ocr_failed`); its `extracted` state is the draft awaiting a human. On approve:
  mint `mint_approval_token(action="bill.commit", resource_id=bill_id, ...)`, then a
  gated `BillCommitter` increases inventory per validated line. Reject → `rejected` +
  reason; the image stays in MinIO. Same primitive as Phase 4, new action string.
- **Inventory INCREASE is the atomic mirror of deduct.** `InventoryRepository.increase`
  is a single `UPDATE inventory SET quantity = quantity + :n WHERE tenant_id=? AND
  product_id=?` (no oversell concern on an increase). For a product with no inventory
  row yet, the committer **upserts** a row (a received bill can be the first stock
  entry for a SKU) — tenant-scoped, audited. Each committed line writes an audit
  entry and a per-bill event breadcrumb.
- **A background worker container (the project's first).** Phase 4 deferred workers
  to Phase 5. Add a `worker` service to `docker-compose.yml` running the SAME image
  as `api` but a different entrypoint (`python -m app.worker`). It polls for (a)
  bills in `ocr_processing` and (b) `knowledge_base_docs` in `pending`/`stale`,
  processing each tenant-scoped. Poll-based (Redis/DB) for now — no Celery this
  phase (constitution: simplest design that satisfies the rules; document that
  Phase 8 may move to a durable queue). The worker opens its own sessions from a
  sessionmaker exactly like the agents/dispatcher do.
- **pgvector store, tenant-filter-before-similarity.** A `kb_chunks` table (and a
  separate `bill_chunks` table, or one `vector_chunks` table discriminated by
  `corpus`) holds `tenant_id`, `source_type`, `source_id`, `content_hash`, the chunk
  text, and a `Vector(N)` embedding column (pgvector, already a dependency). Every
  retrieval query filters `WHERE tenant_id = :t` BEFORE the `<->`/`<=>` similarity
  order-by (constitution I). Two corpora — business knowledge and historical bills —
  are distinguished by `corpus` / table so the OrderAgent searches the right one.
- **Embeddings via a provider-agnostic client, Vault-keyed, stub in tests.** An
  `EmbeddingClient` (Protocol + stub + real) in `app/infra/` mirrors the OCR seam.
  Tests/CI use a deterministic stub (e.g. a hashed pseudo-embedding) so the suite
  stays offline; the real embedder (Gemini embeddings or the GCP key) resolves from
  Vault. The embedding model name + dimension are typed Settings.
- **Frontend: full slice, minimal UI (honor "don't over-polish yet").** Phase 5
  ships an Upload control + a Bills list + a Bill-review screen (image beside the
  fields, confidence flags, approve/reject reusing the Phase 4 approvals shape).
  Functional, RTL, Arabic-correct — not a visual redesign.

---

## New data model (read this before you build)

All Phase 5 tables are **tenant-scoped** (non-nullable, indexed `tenant_id`) and go
through `TenantScopedRepository`. OCR lines map to the existing `products` /
`inventory` tables — no new catalog/stock table.

```
── Supplier bills (OCR artifact, tenant-scoped) ──
supplier_bills     — one uploaded bill and its lifecycle
                     (id, tenant_id, supplier_id → suppliers.id NULL,
                      object_key          TEXT NOT NULL,   -- MinIO path, tenant-prefixed
                      original_filename   TEXT NULL,
                      content_type        TEXT NULL,
                      status              -- see lifecycle below
                      ocr_engine          TEXT NULL,        -- which engine produced the text
                      ocr_text            TEXT NULL,        -- raw OCR output (for re-extract/audit)
                      extracted           JSONB NULL,       -- the BillData (validated structure)
                      bill_date           DATE NULL,        -- parsed from the bill
                      total_amount        NUMERIC NULL,
                      currency            TEXT NULL,         -- "LBP" | "USD" (as printed)
                      min_confidence      NUMERIC NULL,      -- lowest field confidence (review signal)
                      reviewed_by         → users.id NULL,
                      reviewed_at         NULL,
                      reject_reason       TEXT NULL,
                      committed_at        NULL,
                      created_at, updated_at)

supplier_bill_lines — one extracted line, mapped (or not) to a product
                     (id, tenant_id, supplier_bill_id → supplier_bills.id,
                      raw_text            TEXT NULL,         -- the OCR'd line as read
                      name_ar             TEXT NULL,         -- extracted item name
                      quantity            NUMERIC NULL,
                      unit                TEXT NULL,
                      unit_amount         NUMERIC NULL,
                      line_amount         NUMERIC NULL,
                      confidence          NUMERIC NULL,      -- per-line confidence
                      product_id          → products.id NULL, -- mapped target (NULL = unmapped)
                      committed           BOOLEAN DEFAULT false, -- did this line apply to stock?
                      created_at, updated_at)

supplier_bill_events — per-bill breadcrumb trail (mirrors OrderEvent / PO events)
                     (id, tenant_id, supplier_bill_id NULL, event, detail, created_at)

── Vector store (RAG corpora, tenant-scoped) ──
vector_chunks      — embedded chunks for BOTH corpora (or split per corpus)
                     (id, tenant_id, corpus,               -- "knowledge" | "bills"
                      source_type, source_id,              -- e.g. ("product", product_id)
                      content_hash, chunk_index,
                      chunk_text          TEXT NOT NULL,
                      embedding           VECTOR(N) NOT NULL, -- pgvector
                      created_at)
                     INDEX on (tenant_id, corpus); ANN index per pgvector docs.
```

**SupplierBill status lifecycle (single source of truth for the gate + UI):**

```
uploaded ──worker picks up──► ocr_processing ──OCR+extract ok──► extracted
                                   │                                 │
                                   └──OCR/extract fails──► ocr_failed │
                                                                      │ human reviews
                              committed ◄──approve(signed bill.commit token)──┤
                                                                      │
                              rejected  ◄──reject(reason)─────────────┘
```

- `uploaded` — file is in MinIO; the worker has not started. **No stock change.**
- `ocr_processing` — worker is running OCR + extraction.
- `extracted` — `BillData` ready; the draft awaiting a human. **No stock change.**
- `ocr_failed` — OCR/extraction failed; surfaced for re-try / manual entry.
- `committed` — human approved; a signed `bill.commit` token cleared the gate and
  every validated line increased stock. Terminal.
- `rejected` — human declined; carries `reject_reason`. Image stays in MinIO.
  Provisions nothing.

> ⚠️ Same as Phase 4: `status == "extracted"`/`"approved"` is the UI/lifecycle
> marker, NOT the gate. The gate is the signed `bill.commit` token the
> `BillCommitter` verifies. A flipped status alone must never move stock.

---

## The Phase 5 shape (what we wire end-to-end)

```
PART A — Upload + storage (backend)

  Owner ─ POST /bills (multipart) ─► stream straight to MinIO (tenant-prefixed key)
                                     create SupplierBill(status="uploaded")
                                     audit "bill.uploaded"; return 202 + bill id
                                     (NO OCR in the request — ROADMAP pitfall)

PART B — Worker: OCR + extraction (background container)

  worker loop (poll):
    bills in "uploaded"/"ocr_processing":
       fetch image from MinIO (tenant-scoped key)
       preprocess (deskew/denoise/contrast)
       OCREngine.extract(image) → text + per-block confidence   (Cloud Vision; stub in CI)
       BillExtractionAgent.extract(text) → BillData (Tier-1 LLM, Pydantic-validated, retries)
       persist lines + per-field confidence + min_confidence
       status → "extracted"  (or "ocr_failed"); audit + event
    knowledge_base_docs in "pending"/"stale":
       load source content (product/policy/hours), chunk it
       EmbeddingClient.embed(chunks) → vectors        (real embedder; stub in CI)
       upsert vector_chunks(corpus="knowledge", tenant-scoped)
       mark KnowledgeBaseDoc "embedded", embedded_at, content_hash

PART C — Bill review + the HIL commit gate (backend) — REUSES Phase 4 gate

  Owner ─ GET  /bills                       (tenant-scoped list: extracted + ocr_failed)
        ─ GET  /bills/{id}                  (image URL + extracted fields + confidences)
        ─ PUT  /bills/{id}/lines            (owner corrects fields / maps a line to a product)
        ─ POST /bills/{id}/approve  ─► status "committed-pending"; commit review;
        │         └─ mint_approval_token(action="bill.commit", resource_id=bill_id, ...)
        │            THEN fire BillCommitter as a background task (never inline)
        ─ POST /bills/{id}/reject {reason} ─► status "rejected", audited

  BillCommitter.commit(bill, token):
     ActionGate.authorize(token, action="bill.commit", bill_id, tenant) ──invalid──► REFUSE
                                     ──valid──► for each validated, mapped line:
                                                  InventoryRepository.increase(tenant, product_id, qty)
                                                  (upsert if no inventory row)
                                                  mark line committed; audit "bill.line_committed"
                                                status "committed", committed_at, audit "bill.committed"

PART D — RAG retrieval + OrderAgent tool

  OrderAgent gains search_knowledge_base(ctx, query):
     EmbeddingClient.embed(query) → qvec
     vector_chunks WHERE tenant_id=:t AND corpus="knowledge"  ORDER BY embedding <=> qvec  LIMIT k
     (tenant filter BEFORE similarity — constitution I)
     return top chunks → the agent answers "بدكن تسليم لسن الفيل؟" from policy text

PART E — Frontend (minimal, reuses Phase 3/4)

  Upload control   — pick/snap a photo → POST /bills, progress, success/error
  Bills list       — extracted + ocr_failed, status badges
  Bill review      — image beside extracted fields, confidence flags, edit lines,
                     approve (→ commits stock) / reject (reason required)
```

Everything runs **async**; every tenant-scoped read/write/vector-search takes
`tenant_id` from the authenticated JWT (or the worker's per-bill scope), never from
the body. Every bill action is audited.

---

## Phase 5 — Tasks Overview

| Task | What | Branch |
|------|------|--------|
| **— Part A: Upload + storage —** | | |
| 5.1 | MinIO `StorageClient` (`app/infra/storage.py`) — tenant-prefixed keys, stream up/down, Vault creds | `feature/MOD-5-storage-client` |
| 5.2 | `supplier_bills` + `supplier_bill_lines` + `supplier_bill_events` models + migration | `feature/MOD-5-bill-model` |
| 5.3 | `SupplierBillRepository` + `SupplierBillService` (lifecycle writer) | `feature/MOD-5-bill-service` |
| 5.4 | Upload API `POST /bills` (stream→MinIO, 202) + `GET /bills` + schemas | `feature/MOD-5-bill-upload-api` |
| **— Part B: Worker + OCR + extraction —** | | |
| 5.5 | `OCREngine` seam: Protocol + `StubOCREngine` + `CloudVisionOCREngine` + Settings/Vault (`modir/ocr`) | `feature/MOD-5-ocr-engine` |
| 5.6 | Image preprocessing (deskew/denoise/contrast) + confidence formula | `feature/MOD-5-ocr-preprocess` |
| 5.7 | `BillExtractionAgent` graph + Tier-1 extraction tool + Arabic prompt + lifespan wiring | `feature/MOD-5-bill-agent` |
| 5.8 | `worker` entrypoint (`app/worker.py`) polling bills; `worker` service in compose | `feature/MOD-5-worker` |
| 5.9 | OCR pipeline tests (extraction, confidence, ocr_failed, Wall on storage keys) | `feature/MOD-5-ocr-tests` |
| **— Part C: HIL commit gate (reuses Phase 4) —** | | |
| 5.10 | `InventoryRepository.increase` (atomic) + upsert path | `feature/MOD-5-inventory-increase` |
| 5.11 | `BillCommitter` (gated, `bill.commit` action) + `COMMIT_ACTION` | `feature/MOD-5-bill-committer` |
| 5.12 | Bill review API: `GET /bills/{id}`, edit lines, approve (mint token + bg commit), reject | `feature/MOD-5-bill-review-api` |
| 5.13 | HIL commit tests: no-commit-without-token, approve→stock-increase, reject, Wall, audit | `feature/MOD-5-bill-hil-tests` |
| **— Part D: KB embedding + RAG —** | | |
| 5.14 | `EmbeddingClient` seam + `vector_chunks` model/migration (pgvector) + repo (tenant-filter-before-similarity) | `feature/MOD-5-vector-store` |
| 5.15 | Embedding worker leg (drain pending/stale → embed → upsert → mark embedded) | `feature/MOD-5-embed-worker` |
| 5.16 | `search_knowledge_base` tool on OrderAgent + retrieval; RAG + staleness tests | `feature/MOD-5-rag-search` |
| **— Part E: Frontend —** | | |
| 5.17 | Upload control + Bills list | `feature/MOD-5-bills-ui` |
| 5.18 | Bill review screen (image + fields + confidence flags + approve/reject) | `feature/MOD-5-bill-review-ui` |
| **— Close-out —** | | |
| 5.19 | CI: OCR/HIL/RAG suites run (stubs offline); forbidden patterns clean; worker image builds; frontend green; `docs/DECISIONS.md` | `chore/MOD-5-ci` |

Each task is a separate branch and PR. No exceptions. **Pause for approval after each.**

> **Why this order (recorded):** storage + the bill record (A) are the physical
> truth; the worker that fills them (B) comes next; only once a bill can reach
> `extracted` does the human commit gate (C) have something to govern — and it
> reuses Phase 4's gate, so it is small. KB embedding + RAG (D) is independent
> plumbing that rides the same worker. The UI (E) renders a system that already
> behaves correctly. Within A–C, the gate (constitution V) is proven by a
> no-commit-without-token test before any UI exists.

---

## Task 5.1 — MinIO `StorageClient`

**Branch:** `feature/MOD-5-storage-client`

`app/infra/storage.py` — the ONE place an object enters/leaves MinIO, tenant-safe.
- Build an async-friendly client over the MinIO/S3 API using credentials from
  `Settings` (resolved from Vault `modir/minio`, already in `secrets_map`). Use the
  `minio` SDK or `aioboto3`/`httpx` — whichever keeps blocking calls off the event
  loop (wrap sync SDK calls in `asyncio.to_thread`; **never** block the loop —
  constitution async rule). Do NOT use `requests`.
- `object_key(tenant_id, bill_id, filename) -> str` — builds a **tenant-prefixed**
  key, e.g. `bills/{tenant_id}/{bill_id}/{safe_filename}`. The tenant prefix is the
  Wall for storage: a key is only ever constructed/read for the caller's tenant.
- `put_stream(key, fileobj, content_type)` and `get_stream(key) -> bytes/stream`
  and `presigned_get(key, ttl) -> str` (for the review screen to show the image).
- Ensure the bucket exists at startup (idempotent), bucket name from Settings
  (`settings.minio_bucket`, new typed field, default `modir-bills`).
- New typed Settings: `minio_bucket`, `minio_secure` (TLS off in dev).

**Commit message:**
```
feat(infra): MinIO storage client — tenant-prefixed keys, streamed up/down

The one place an object enters or leaves MinIO. Keys are tenant-prefixed
(bills/{tenant_id}/{bill_id}/...) so a key is only ever built or read inside the
caller's tenant — the Wall for object storage. Credentials resolve from Vault
(modir/minio); blocking SDK calls run off the event loop; never requests.
```

**Verification (live stack):**
- Put a small object under tenant A's prefix, get it back byte-identical.
- A presigned GET URL fetches the object; expires after the TTL.
- `grep -rn "import requests" backend/app/` still clean; no blocking call on the loop.

---

## Task 5.2 — `supplier_bills` + lines + events Models + Migration

**Branch:** `feature/MOD-5-bill-model`

Add the three tenant-scoped tables from the model section to `app/db/models.py`
(after the Phase 4 PO models). Field shapes per the model section. `SupplierBill`
references `suppliers` (nullable — the supplier may be unknown until extraction) and
holds the lifecycle status (default `"uploaded"`); `SupplierBillLine` references the
bill and (nullable) a `products.id` mapping target; `SupplierBillEvent` mirrors
`OrderEvent`/`PurchaseOrderEvent`.

Document the status lifecycle in the `SupplierBill` docstring (the diagram above),
and state explicitly that **status is the lifecycle marker, not the security gate**
— the gate is the signed `bill.commit` token (Task 5.11). Autogenerate inside the
`moudir-api-1` container (host `.env` uses Docker hostnames), **review by hand**
(FKs, indexes, the tenant_id non-nullable index, JSONB column), round-trip.

**Commit message:**
```
feat(models): supplier_bills + lines + events (tenant-scoped) + migration

The OCR artifact: a bill moves uploaded → ocr_processing → extracted →
committed/rejected (or ocr_failed). status is the lifecycle marker for the UI; the
actual stock-commit gate is the signed bill.commit token (5.11), so a flipped
status alone can never move stock. Lines map to the one products table; per-bill
event trail mirrors OrderEvent. Migration reviewed, round-trips.
```

**Verification:**
- `from app.db.models import SupplierBill, SupplierBillLine, SupplierBillEvent` clean.
- `alembic upgrade head` / `downgrade -1` round-trips on a fresh DB.
- All three tables carry a non-nullable indexed `tenant_id`.

---

## Task 5.3 — `SupplierBillRepository` + `SupplierBillService`

**Branch:** `feature/MOD-5-bill-service`

- `app/repositories/supplier_bills.py` (extends `TenantScopedRepository`):
  `list_for_review(tenant_id, *, statuses, limit, offset)` (extracted + ocr_failed),
  `list_claimable(statuses=("uploaded",), limit)` for the worker, `get_with_lines`,
  lines joined to product where mapped (JOINs scoped both sides).
- `app/services/supplier_bills.py` → `SupplierBillService` is the ONLY writer of
  bill state (mirrors `PurchaseOrderService`): `create_uploaded(...)`,
  `mark_processing`, `save_extraction(text, BillData, lines, min_confidence)`,
  `mark_ocr_failed(error)`, `approve(tenant, bill_id, approver_id)` (→ a pre-commit
  state; the API mints the token + fires the committer after commit — same split as
  PO approve), `reject(reason)`, `mark_committed`. Each transition validates the
  current status (→ 409 on a bad move) and is audited + writes an event. Flushes,
  does not commit (joins the caller's transaction) — same discipline as the PO
  service.
- Add domain errors `SupplierBillNotFound`, `InvalidBillTransition` mirroring the PO
  errors.

**Commit message:**
```
feat(bills): supplier-bill repository + service (the only bill-state writer)

SupplierBillService owns every bill transition (upload/process/extract/approve/
reject/commit), each validating the current status and audited. The repo surfaces
the review list and the worker's claimable queue, joining lines to the catalog
scoped on both sides. Token minting + the stock commit live above this (5.11/5.12).
```

**Verification:**
- create_uploaded → mark_processing → save_extraction → approve walks the machine;
  a bad transition (approve an `uploaded` bill) → 409.
- Every transition writes an audit row + a bill event.
- A cross-tenant `get` returns None (404 upstream) — the Wall.

---

## Task 5.4 — Upload API `POST /bills` + `GET /bills`

**Branch:** `feature/MOD-5-bill-upload-api`

- `app/api/schemas/bills.py`: `BillRead` (status, supplier, min_confidence,
  bill_date, totals, image URL), `BillLineRead`, `BillsPage`.
- `app/api/bills.py`:
  - `POST /bills` — accepts a multipart image (validate content-type + size),
    **streams it straight to MinIO** via `StorageClient` (never local disk —
    ROADMAP pitfall), creates `SupplierBill(status="uploaded")`, audits
    `bill.uploaded`, returns **202** with the bill id. **No OCR here** (the worker
    does it).
  - `GET /bills` — paginated review list (extracted + ocr_failed), tenant-scoped.
  - Tenant scope from `get_current_user` → `user.tenant_id`, never the body.
- Mount the router in `create_app`.

**Commit message:**
```
feat(api): bill upload + list — stream to MinIO, OCR deferred to the worker

POST /bills streams the photo straight to MinIO under a tenant-prefixed key and
records a SupplierBill in `uploaded`, returning 202 immediately — OCR never runs in
the request (it would hang for seconds; the worker handles it). GET /bills is the
tenant-scoped review list. tenant_id comes from the JWT, never the body; uploads are
audited.
```

**Verification (live stack):**
- Upload an image → 202 + bill id; object exists in MinIO under the tenant prefix;
  the request returns fast (no OCR inline).
- Tenant A's `GET /bills` never shows tenant B's bills.
- A non-image / oversize upload → 4xx, nothing stored.

---

## Task 5.5 — `OCREngine` Seam + Cloud Vision + Stub + Settings/Vault

**Branch:** `feature/MOD-5-ocr-engine`

Provider-agnostic OCR, mirroring `EmailSender`/`SupplierDispatcher`:
- `app/infra/ocr/engine.py`: an `OCREngine` Protocol — `extract(image_bytes) ->
  OCRResult` where `OCRResult` carries the full text and per-block text+confidence.
- `StubOCREngine` — returns deterministic canned text/confidence for **tests + CI**
  and the dev default when no GCP key is set (offline, like the mocked LLM).
- `CloudVisionOCREngine` — the real engine; GCP credentials resolve from **Vault**
  (`modir/ocr` — add to `secrets_map` AND the `vault-seed` entrypoint, kept in sync;
  add the typed Settings field). Calls the Vision API; never `requests`; blocking
  client calls run via `asyncio.to_thread`.
- `Settings.ocr_mode` (`stub` | `cloud_vision` | `tesseract`) selects the engine
  via a small factory (like the dispatch-mode switch). The engine is built once in
  `lifespan` (`app.state.ocr_engine`) and injected into the worker.

Record the cost-vs-accuracy choice in `docs/DECISIONS.md` (gitignored; final copy in
Task 5.19). `TesseractOCREngine` is optional this task — document it as the offline
fallback if not implemented.

**Commit message:**
```
feat(ocr): OCREngine seam — Cloud Vision (real) + stub, Vault-keyed, mode-selected

OCR behind a provider-agnostic Protocol like EmailSender/LLMRouter: StubOCREngine
keeps CI/tests offline; CloudVisionOCREngine (GCP key from Vault, modir/ocr) is the
real Lebanese-Arabic engine, verified on the host (DNS is blocked in-container).
ocr_mode selects the engine; blocking calls run off the event loop; never requests.
```

**Verification:**
- Stub mode: `extract` returns the canned `OCRResult` deterministically (CI path).
- `grep -rn "import requests" backend/app/` clean; Vision client confined to
  `app/infra/ocr/`.
- (Host venv) cloud_vision mode against a sample image returns text + confidences.

---

## Task 5.6 — Image Preprocessing + Confidence Formula

**Branch:** `feature/MOD-5-ocr-preprocess`

Raw phone photos OCR poorly (ROADMAP pitfall). Before OCR:
- `app/infra/ocr/preprocess.py`: deskew, denoise, grayscale/contrast normalize
  (Pillow / OpenCV — add the dep). Pure function `preprocess(image_bytes) ->
  image_bytes`; documented, testable on a fixture image.
- Define the **per-field confidence** formula (documented): combine the OCR block
  confidence with the extraction step's certainty into a `0..1` score per line/field;
  compute `min_confidence` for the bill. `settings.ocr_confidence_review_threshold`
  (typed, default e.g. 0.75) flags fields needing attention in the UI — a **review
  signal, not an auto-commit switch** (every bill still goes to a human in Phase 5).

**Commit message:**
```
feat(ocr): image preprocessing + per-field confidence scoring

Deskew/denoise/contrast-normalize a raw phone photo before OCR (raw photos OCR
badly). Each field gets a documented 0..1 confidence (OCR block confidence ×
extraction certainty); min_confidence flags bills for closer review. The threshold
is a UI signal, never an auto-commit — every bill goes to a human in Phase 5.
```

**Verification:**
- `preprocess` on a skewed fixture produces a deskewed image (visual/asserted).
- The confidence formula yields the documented score on a known stub result; a
  low-confidence field is flagged at/below the threshold.

---

## Task 5.7 — `BillExtractionAgent` Graph + Extraction Tool + Arabic Prompt + Lifespan

**Branch:** `feature/MOD-5-bill-agent`

Mirror the InventoryAgent EXACTLY: a compiled `StateGraph` built ONCE, per-call
`ToolContext` (tenant-scoped session + router + settings + the OCR text) via the
graph `config`, the single instance on `app.state`, built in `lifespan`. Code under
`app/agents/ocr/` (`agent.py`, `tools.py`, `schemas.py`).
- `BillData` Pydantic schema: supplier name, bill date, currency, line items
  (`name_ar`, quantity, unit, unit_amount, line_amount), total. Each field’s
  confidence tracked alongside.
- Tool `extract_bill(ctx, ocr_text) -> BillData` — **Tier-1 LLM** structures the OCR
  text (constitution IV: the LLM structures text, it does not read pixels). Output
  Pydantic-validated; bad output retries up to `settings.llm_max_retries` then
  degrades gracefully (mark fields low-confidence / `ocr_failed`), never crashes —
  the established `parse_order`/`_draft_note` pattern.
- Optional best-effort **product mapping**: match each extracted `name_ar` to a
  `products` row for the tenant (exact/fuzzy on name) → set `product_id`; unmatched
  lines stay unmapped for the human to map in review (never auto-create catalog
  rows).
- Prompt copy in `backend/prompts/bill_agent_ar.py` (Arabic in files, never inline).
- The agent exposes `extract_for_bill(tenant_id, ocr_text)` the worker calls.
- Wire `app.state.bill_agent = BillExtractionAgent(router, settings, sessionmaker)`
  in `lifespan` next to the other agents.

**Commit message:**
```
feat(agent): BillExtractionAgent — structure OCR text into a validated BillData

A standalone LangGraph graph mirroring the InventoryAgent (built once in lifespan,
per-call ToolContext via config, concurrency-safe). The Tier-1 LLM structures the
OCR TEXT into BillData (constitution IV — the LLM never reads pixels); output is
Pydantic-validated and bad output retries then degrades, never crashes. Lines are
best-effort mapped to the one products table; unmapped lines wait for the human.
Prompt copy lives in prompts/bill_agent_ar.py.
```

**Verification:**
- Feed canned stub OCR text → a valid `BillData` with lines + confidences; no 500 on
  a malformed LLM response (retries → graceful degrade).
- `grep -rn "import google\|langchain_google" backend/app/` shows the provider SDK
  only under `app/agents/llm/`.

---

## Task 5.8 — `worker` Entrypoint + Compose Service

**Branch:** `feature/MOD-5-worker`

The project's first background worker (Phase 4 deferred this).
- `app/worker.py` → an async poll loop: claim `SupplierBill`s in `uploaded` (set
  `ocr_processing`), fetch the image from MinIO, `preprocess`, `ocr_engine.extract`,
  `bill_agent.extract_for_bill`, persist lines + confidences, set `extracted` (or
  `ocr_failed`). Tenant-scoped per bill. Opens its own sessions from a sessionmaker
  (like the dispatcher). Builds its OWN singletons (engine, agent, storage) the same
  way `lifespan` does — share a small builder so api + worker construct identically.
  Poll interval from `settings.worker_poll_seconds`. Graceful shutdown on SIGTERM.
- `docker-compose.yml`: add a `worker` service — SAME build context/image as `api`,
  `command: ["python", "-m", "app.worker"]`, same `env_file`, `depends_on`
  db/redis/vault/minio/migrate/vault-seed (same conditions as `api`). No new port.
- The KB-embedding leg is added to this loop in Task 5.15.

**Commit message:**
```
feat(worker): background OCR worker + compose service (the project's first worker)

app/worker.py polls for uploaded bills, pulls the image from MinIO, preprocesses,
runs OCR + extraction, and lands the bill in `extracted` (or `ocr_failed`) — OCR
never blocks the api container (ROADMAP pitfall). Same image as api, different
entrypoint; builds its own singletons via the shared builder. Poll-based for now;
Phase 8 may move to a durable queue.
```

**Verification (live stack):**
- Upload a bill → within the poll interval it becomes `extracted` with lines; the
  api container never blocked.
- A forced extraction failure → `ocr_failed`, error recorded, worker keeps running.
- The `worker` container starts healthy after `migrate`/`vault-seed`.

---

## Task 5.9 — OCR Pipeline Tests

**Branch:** `feature/MOD-5-ocr-tests`

**File: `backend/tests/test_ocr_pipeline.py`** (stub OCR + mocked LLM — offline):
1. Stub OCR text → extraction → a `SupplierBill` reaches `extracted` with the right
   lines, per-field confidence, and `min_confidence`.
2. A malformed LLM extraction retries then degrades → `ocr_failed` (or low-confidence
   extracted), never a crash.
3. **The Wall on storage:** an object key built for tenant A is never readable as
   tenant B; a bill `get` across tenants returns None (404 upstream).
4. Bill lifecycle transitions validate status (approve an `uploaded` bill → 409).
5. Confidence threshold flags the documented low-confidence field.

**Commit message:**
```
test(ocr): extraction, confidence, ocr_failed degrade, and the storage Wall

Stub OCR + mocked LLM keep the suite offline: a bill reaches `extracted` with lines
and per-field confidence; a malformed extraction degrades to ocr_failed without
crashing; a tenant-prefixed object key can't be read across the Wall; lifecycle
transitions are guarded. Confidence flags the right field for review.
```

**Verification:**
- `cd backend && uv run pytest tests/test_ocr_pipeline.py -v` green.
- Temporarily drop the tenant prefix from `object_key` → the Wall test FAILS
  (confirm, then revert).

---

## Task 5.10 — `InventoryRepository.increase` (Atomic) + Upsert

**Branch:** `feature/MOD-5-inventory-increase`

Mirror the existing guarded `deduct`:
- `increase(tenant_id, product_id, qty) -> bool` — a single `update(Inventory)
  .where(tenant_id==, product_id==).values(quantity = Inventory.quantity + qty)`;
  return `rowcount == 1`. (An increase never violates the CHECK; no oversell guard
  needed, but it stays a single atomic UPDATE, not read-then-write.)
- `ensure_row(tenant_id, product_id) -> Inventory` — upsert: if no inventory row
  exists for a product (a received bill can be the first stock for a SKU), create one
  at quantity 0 (tenant-scoped) so `increase` has a row to hit. Use
  `INSERT ... ON CONFLICT (tenant_id, product_id) DO NOTHING` to stay race-safe.

**Commit message:**
```
feat(repo): inventory increase (atomic) + ensure_row upsert for received stock

increase() is the mirror of deduct() — a single guarded UPDATE that adds received
quantity. ensure_row upserts a zero-quantity row (ON CONFLICT DO NOTHING) so a
bill can be the first time a SKU enters stock, race-safe. Both stay tenant-scoped.
```

**Verification:**
- Unit test: seed quantity 5, `increase(.., 3)` → True, level 8; `ensure_row` on a
  new product creates a 0-row, then `increase` lifts it.
- An `increase` for another tenant's product affects nothing (the Wall).

---

## Task 5.11 — `BillCommitter` (Gated, `bill.commit` Action)

**Branch:** `feature/MOD-5-bill-committer`

`app/infra/bill_committer.py` — the ONE place an OCR'd bill changes stock, **gated**,
mirroring `SupplierDispatcher`:
- Add `COMMIT_ACTION = "bill.commit"` (next to `DISPATCH_ACTION`). It is a NEW action
  string on the EXISTING `ActionGate` — no new gate.
- `BillCommitter.commit(bill, token)`:
  - **FIRST**, `ActionGate.authorize(settings, token, action=COMMIT_ACTION,
    resource_id=bill.id, tenant_id=bill.tenant_id)` — refuse (raise
    `UnauthorizedAction`) on any absent/forged/expired/mismatched token. Nothing is
    committed; no stock moves.
  - Then, in one transaction: for each validated, **mapped** line,
    `InventoryRepository.ensure_row` + `increase(tenant, product_id, qty)`, mark the
    line `committed`, audit `bill.line_committed`. Unmapped/invalid lines are skipped
    and logged (surfaced for the human — they shouldn't have been approvable; the
    review API enforces mapping before approve).
  - Set bill `committed`, `committed_at`, audit `bill.committed`. Opens its own
    session (runs as a background task after the approve commit), like the dispatcher.

**Commit message:**
```
feat(hil): BillCommitter — gated stock increase from an approved OCR bill

The one place an OCR'd bill changes stock, and it changes nothing without a valid
signed bill.commit token (the SAME ActionGate as Phase 4 — new action string, no new
gate). On authorize, each validated mapped line increases inventory in one
transaction, audited; an absent/forged/mismatched token raises UnauthorizedAction
and no stock moves. Runs out of band after the approve commit, like the dispatcher.
```

**Verification:**
- A valid `bill.commit` token → mapped lines lift inventory; bill `committed`, audit
  present.
- A token for a different bill / tenant / action → refused, no stock change.
- An expired/tampered token → refused.

---

## Task 5.12 — Bill Review API: View, Edit Lines, Approve, Reject

**Branch:** `feature/MOD-5-bill-review-api`

The owner's HIL control surface for bills, mirroring the Phase 4 approvals API
(tenant-scoped via `get_current_user`, all audited):
- `GET /bills/{id}` — the bill with a **presigned image URL** + extracted fields +
  per-field confidences + mapped/unmapped lines.
- `PUT /bills/{id}/lines` — the owner corrects fields and **maps a line to a
  product** (required before that line can commit). Scoped; only `extracted` bills
  are editable (409 otherwise).
- `POST /bills/{id}/approve` — `SupplierBillService.approve(...)` + COMMIT; THEN
  **mint `mint_approval_token(action="bill.commit", resource_id=bill_id, ...)`** and
  fire `BillCommitter.commit` as a **background task** — never inline (a slow commit
  must not hang the call; same rule as PO dispatch). Returns immediately. Reject any
  approve where required lines are unmapped (422).
- `POST /bills/{id}/reject` — reason required; image stays in MinIO; audited.
- Schemas in `app/api/schemas/bills.py`; mount in `create_app`.

**Commit message:**
```
feat(api): bill review — view/edit/approve/reject, gate-enforced, audited

GET /bills/{id} returns the bill with a presigned image URL and per-field
confidences; PUT .../lines lets the owner correct fields and map lines to products.
Approve commits the review, mints a signed bill.commit token, and fires the gated
BillCommitter as a background task (never blocking the call); reject requires a
reason and leaves the image in MinIO. Every action is tenant-scoped and audited.
```

**Verification (live stack):**
- View a bill → image shows via presigned URL; low-confidence fields flagged.
- Map lines, approve → background commit lifts inventory; the call returns fast.
- Approve with an unmapped required line → 422. Reject without reason → 422.
- Tenant A cannot view/approve tenant B's bill (404).

---

## Task 5.13 — HIL Commit Tests

**Branch:** `feature/MOD-5-bill-hil-tests`

**File: `backend/tests/test_bill_hil.py`** (stubs offline):
1. **The gate holds:** `BillCommitter.commit` with no token / forged / wrong-bill
   token → refused, no stock change, bill not `committed` (the constitution-V test).
2. **Happy path:** extracted → map lines → approve → (token minted) → commit →
   inventory increased by each line; bill `committed`, `committed_at` set.
3. **Reject path:** reject → `rejected` + reason; no stock change; image still in
   MinIO.
4. **No auto-commit:** an `extracted` bill never moved stock without an approval.
5. **The Wall:** tenant A's owner cannot view/approve/commit tenant B's bill; the
   commit JOINs stay tenant-scoped; inventory increase stays in A's scope.
6. **Audit:** uploaded, extracted, approved, committed (+ line_committed), rejected
   each produce an audit row.

**Commit message:**
```
test(hil): no bill commits stock without a signed token; approve→increase; reject

Proves the bill commit reuses the single execution gate: commit refuses an
absent/forged/mismatched bill.commit token and moves no stock. Happy path maps
lines → approves → commits → inventory rises; reject changes nothing and keeps the
image; the Wall holds across view/approve/commit; every action is audited. OCR
stubbed, LLM mocked.
```

**Verification:**
- `cd backend && uv run pytest tests/test_bill_hil.py -v` green.
- Temporarily make `BillCommitter` skip `ActionGate.authorize` → the
  no-commit-without-token test FAILS (confirm, then revert).

---

## Task 5.14 — `EmbeddingClient` Seam + `vector_chunks` (pgvector) + Repo

**Branch:** `feature/MOD-5-vector-store`

- `app/infra/embeddings.py`: an `EmbeddingClient` Protocol — `embed(texts) ->
  list[vector]`. `StubEmbeddingClient` (deterministic hashed pseudo-embedding for
  CI/tests, offline), and the real client (Gemini embeddings or the GCP key from
  Vault). `Settings.embedding_mode` selects; `embedding_model` + `embedding_dim`
  typed. Built once in `lifespan` / the worker builder.
- `vector_chunks` model (pgvector `Vector(embedding_dim)` column) + migration
  (autogenerated in-container, hand-reviewed; add the pgvector ANN index per the
  pgvector docs — e.g. `ivfflat`/`hnsw` on the embedding, plus a btree on
  `(tenant_id, corpus)`). The `pgvector` Python + the `pgvector/pgvector:pg16` image
  are already present.
- `app/repositories/vector_chunks.py` (extends base): `upsert_chunks(tenant_id,
  corpus, source_type, source_id, content_hash, chunks_with_vectors)` (replace prior
  chunks for that source on re-embed), and `search(tenant_id, corpus, qvec, k)` —
  the query **filters `WHERE tenant_id = :t AND corpus = :c` BEFORE the similarity
  order-by** (constitution I: tenant filter before similarity, never after).

**Commit message:**
```
feat(rag): EmbeddingClient seam + vector_chunks (pgvector), tenant-filter-first

Embeddings behind a Protocol like the OCR/email seams: a deterministic stub keeps CI
offline, the real client's key resolves from Vault. vector_chunks stores tenant-
scoped chunks for both corpora; search() filters by tenant_id (and corpus) BEFORE
the similarity order-by — the Wall for vector search, exactly as the constitution
requires. ANN index per pgvector docs.
```

**Verification:**
- Upsert chunks for tenant A; `search` returns them ranked; the same query under
  tenant B returns nothing (tenant filter precedes similarity).
- Re-upsert for the same source replaces the prior chunks (no duplicates).
- `alembic upgrade head` / `downgrade -1` round-trips; the vector column + index exist.

---

## Task 5.15 — Embedding Worker Leg (Drain `pending`/`stale`)

**Branch:** `feature/MOD-5-embed-worker`

Add a second leg to the worker loop (Task 5.8):
- Claim `KnowledgeBaseDoc` rows in `pending`/`stale` (tenant-scoped per row). For
  each: load the source content (product description/name, policy value, hours note —
  by `source_type`/`source_id`), chunk it (a small documented chunker — the
  `rag-pipeline` skill), `EmbeddingClient.embed`, `upsert_chunks(corpus="knowledge")`,
  then `KnowledgeBaseDoc` → `embedded`, set `embedded_at` (the repo already tracks
  `content_hash`). A re-edited product flips its row to `stale` (the Phase 1 hook
  already does this); the worker re-embeds within the poll interval.
- The historical-bills corpus: when a bill reaches `committed`, enqueue its lines'
  text for embedding under `corpus="bills"` (so Phase 6 can retrieve historical
  bills). Keep it simple — a committed-bill hook that the worker drains, or embed
  inline in the committer; document the choice.

**Commit message:**
```
feat(rag): worker embeds pending/stale knowledge_base_docs + committed bills

The worker drains the knowledge_base_docs rows that have sat pending since Phase 1/3:
load source content, chunk, embed (stub in CI), upsert tenant-scoped vectors, mark
embedded. A re-edited product flips to stale (Phase 1 hook) and is re-embedded within
the poll interval. Committed bills feed a separate `bills` corpus for Phase 6.
```

**Verification (live stack):**
- With pending rows present, the worker embeds them → rows become `embedded`,
  vectors appear in `vector_chunks` (corpus `knowledge`).
- Edit a product price → its row flips `stale` → re-embedded within ~the poll
  interval; the chunk content updates.
- A committed bill produces `bills`-corpus chunks.

---

## Task 5.16 — `search_knowledge_base` Tool on the OrderAgent + RAG Tests

**Branch:** `feature/MOD-5-rag-search`

- Add `search_knowledge_base(ctx, query)` to the OrderAgent's tools
  (`app/agents/order/tools.py`): embed the query via `EmbeddingClient`,
  `VectorChunkRepository.search(tenant_id, corpus="knowledge", qvec, k)` (tenant
  filter before similarity), return the top chunks. Wire it into the OrderAgent graph
  so a policy/hours/delivery question routes to retrieval (allowlist stays
  role-correct — it's a read tool, fine for the customer path). Prompt copy for the
  retrieval-answer step in the Arabic prompts file.
- **File: `backend/tests/test_rag_search.py`** (stub embeddings): seed policy chunks
  for tenant A; a query like "بدكن تسليم لسن الفيل؟" retrieves the delivery-zone
  policy chunk; the SAME query under tenant B retrieves nothing (the Wall on vector
  search). A stale→re-embed cycle updates what's retrieved.

**Commit message:**
```
feat(agent): OrderAgent search_knowledge_base — answer policy questions from RAG

The OrderAgent gains a read-only retrieval tool: embed the customer's question,
search this tenant's `knowledge` corpus (tenant filter before similarity), and
answer from the policy/hours chunks. "بدكن تسليم لسن الفيل؟" now returns the
delivery-zone policy. Tests prove retrieval is tenant-scoped and reflects re-embeds.
```

**Verification:**
- `cd backend && uv run pytest tests/test_rag_search.py -v` green.
- (Live) ask a delivery-zone question over the customer path → the agent answers from
  the policy, not a hallucination; tenant B's policies never leak.

---

## Task 5.17 — Upload Control + Bills List (Frontend)

**Branch:** `feature/MOD-5-bills-ui`

Reuse the Phase 3/4 app shell, `apiClient`, i18n dictionary, tokens.
- An **Upload** control: pick/snap a photo → `POST /bills` (multipart), progress,
  success/error toast, then the bill appears in the list as `uploaded`/`processing`.
- A **Bills list** from `GET /bills`: each bill shows supplier (if known), date,
  total, a status badge (uploaded/processing/extracted/ocr_failed/committed/rejected
  — color+icon+Arabic label, never color alone), newest first. Polls (like the order
  feed) so `extracted` appears without a manual refresh.
- Mobile cards at 360px / table ≥1024px. All copy via the i18n dictionary. Honor
  "don't over-polish yet" — functional, RTL, Arabic-correct.

**Commit message:**
```
feat(frontend): bill upload control + bills list

Reuses the Phase 3/4 shell and i18n: snap/upload a photo (multipart → POST /bills)
with progress and a result toast, then watch it move through statuses in a polled
bills list (badge = color+icon+label). Cards at 360px, table on desktop; all copy
from the dictionary.
```

**Verification:**
- Upload a photo → it appears and progresses to `extracted` without a manual reload.
- Readable/usable at 360px, RTL; status badges are accessible (not color-only).

---

## Task 5.18 — Bill Review Screen (Frontend)

**Branch:** `feature/MOD-5-bill-review-ui`

The owner-facing HIL review, modeled on the Phase 4 approvals UX:
- The bill **image side-by-side** with the extracted fields (presigned URL from
  `GET /bills/{id}`). Each field/line shows its **confidence**; low-confidence fields
  are visibly flagged (icon + label) so the owner knows where to look.
- **Edit lines** (correct name/qty/amount, **map each line to a product** via a
  product picker) → `PUT /bills/{id}/lines`. Required-line mapping is enforced before
  approve (mirror the disabled-until-valid pattern).
- **Approve** (commits stock — the row moves to `committed` on the next poll) and
  **Reject** (reason required — reuse the Phase 4 reject UX) → the review endpoints.
- Empty/skeleton/error states; Arabic via the dictionary; 360px-first.

**Commit message:**
```
feat(frontend): bill review screen — image beside fields, confidence flags, approve

The tenant-owner HIL review (modeled on the Phase 4 approvals UI): the bill image
side-by-side with extracted fields, per-field confidence flags, line editing with a
product-mapping picker (required before approve), and approve (commits stock) /
reject (reason required). Skeleton/empty states, RTL, all copy from the dictionary;
works at 360px.
```

**Verification (live demo end-to-end):**
- Upload a bill → it OCRs to `extracted` → open review → image + fields show,
  low-confidence flagged → map lines → approve → inventory increases and the bill
  flips to `committed`. Reject requires a reason.

---

## Task 5.19 — CI: OCR/HIL/RAG Suites + Forbidden Patterns + Worker Image + DECISIONS.md

**Branch:** `chore/MOD-5-ci`

**Edit `.github/workflows/ci.yml`:**
- The backend job picks up `test_ocr_pipeline.py`, `test_bill_hil.py`,
  `test_rag_search.py` under the existing `uv run pytest backend/tests` step (OCR
  stubbed, LLM mocked, embeddings stubbed — the suite stays offline; live OCR/Vision
  is verified on the host).
- Reaffirm forbidden-patterns: no `os.getenv` / `print(` / `import requests` in
  `backend/app/`; the OCR/storage/embedding clients use `httpx`/SDKs off the event
  loop, never `requests`; the Vision/embedding SDK and the provider LLM SDK stay
  confined to their modules (`app/infra/ocr/`, `app/infra/embeddings.py`,
  `app/agents/llm/`). The single documented LangSmith `os.environ` write remains the
  only exception.
- Ensure the **worker image builds** (same context as api) and, if compose is part of
  CI, that the `worker` service comes up.
- The frontend job covers the new Bills + Review pages via `tsc --noEmit` + `vite
  build`.
- Write the final **`docs/DECISIONS.md`** entries (gitignored — local only per the
  standing rule): OCR engine (Cloud Vision, cost vs accuracy), confidence threshold
  + why every bill goes to a human in Phase 5, bill→stock via the reused gate,
  poll-worker vs queue (Phase 8), tenant-filter-before-similarity.

**Commit message:**
```
ci: run Phase 5 OCR/HIL/RAG suites; reaffirm forbidden patterns; build worker

The backend job now runs the OCR pipeline, bill-commit HIL, and RAG suites (OCR
stubbed, LLM mocked, embeddings stubbed). Reaffirms no os.getenv/print/import
requests in backend/app; the Vision/embedding/provider SDKs stay confined to their
modules. The worker image builds and the frontend job builds the new bill pages. A
regression fails the build.
```

**Verification:**
- Push the branch; CI runs the full backend suite + the frontend build + the worker
  image build — all green.
- Introduce `import requests` in `app/infra/ocr/` → CI fails; revert → green.

---

## Phase 5 — Definition of Done

**OCR pipeline:**
- [ ] Upload a real Lebanese supplier bill image. Within ~30s, structured data
      appears for review (the worker OCRs + extracts; the api never blocked).
- [ ] Each extracted field has a confidence score visible in the UI; low-confidence
      fields are flagged.
- [ ] OCR runs in the `worker` container, never the `api` container.
- [ ] The bill image lives in MinIO under a tenant-prefixed key — tenant A can never
      fetch tenant B's bill (proven by a test).
- [ ] Rejected bills stay in MinIO with the rejection reason logged.

**Bill → stock through the SAME HIL gate:**
- [ ] Approved bills increase inventory automatically, with audit log entries per
      line and per bill.
- [ ] **No code path commits a bill to stock without a valid signed `bill.commit`
      token** — a test proves an absent/forged/mismatched token is refused
      (constitution V). The bill commit reuses `ActionGate`, not a new gate.
- [ ] Approve fires the commit as a background task after commit — a slow commit
      never hangs the approve call.

**Knowledge base + RAG:**
- [ ] All `pending` `knowledge_base_docs` rows from Phase 1/3 are now `embedded` in
      pgvector (tenant-scoped vectors).
- [ ] Update a product price → its `knowledge_base_docs` row becomes `stale` → the
      worker re-embeds it within ~60s.
- [ ] A customer asks "بدكن تسليم لسن الفيل؟" — the OrderAgent retrieves the delivery-
      zone policy from the knowledge base and answers correctly.
- [ ] Historical (committed) bills are searchable via RAG under a separate corpus
      (Phase 6 uses this for forecasting context).
- [ ] **Vector search filters by `tenant_id` BEFORE similarity** — a test proves
      tenant B's chunks never surface for tenant A.

**Cross-cutting (the Wall + forbidden patterns):**
- [ ] Every Phase 5 table carries a non-nullable indexed `tenant_id`; every new repo
      method filters by it; bill/inventory and vector JOINs/queries are tenant-scoped.
- [ ] A test proves tenant A cannot read/commit/search tenant B's bills, inventory,
      or vectors.
- [ ] `grep -rn "os.getenv\|print(\|import requests" backend/app/` returns nothing;
      the Vision/embedding/provider SDKs stay confined to their modules; blocking SDK
      calls run off the event loop.
- [ ] CI is green: backend (migrations + full pytest, OCR stubbed, LLM mocked,
      embeddings stubbed) AND the frontend job AND the worker image build.

**Demoable end-to-end:**
- [ ] Owner logs in → snaps/uploads a paper bill → the worker OCRs it → owner reviews
      (image beside fields, confidence flags) → maps lines → approves → stock
      increases and the bill flips to `committed` → and a customer's policy question
      is answered from RAG — all RTL, Lebanese Arabic, on a 360px screen.

## Phase 5 — Defend-it Preparation

Practice answering these out loud (these become `docs/PHASE_5_DEFEND_IT.md`):

1. Why did you choose Google Cloud Vision over Tesseract? Where is that recorded, and
   how is the engine still swappable without touching callers?
2. Walk me through what happens from "Abu Khaled uploads a photo" to "inventory
   updated" — name every component and where the tenant scope and the gate sit.
3. Where does the bill image actually live, and what stops tenant A from seeing
   tenant B's bills? Show me the key construction.
4. Show me where the HIL gate lives for a bill commit. Why is it the SAME gate as the
   purchase-order dispatch, and why is `status == "committed"` *not* the gate?
5. What's your confidence threshold, and what does it actually control? Why does every
   bill still go to a human in Phase 5?
6. Why does OCR run in a worker and not the upload request? What breaks if you OCR
   inline?
7. The constitution says "vector search filters by `tenant_id` before similarity."
   Show me the exact query. Why does filtering *after* similarity leak?
8. The LLM never reads the image — what does it do, and what does the OCR engine do?
   (Constitution IV: ML/OCR vs LLM.)
9. A product price changes after embedding. Walk me through how the vector store stays
   in sync — which row flips, who re-embeds, how fast.
10. Prove the Wall holds for bills AND vectors: show the line that keeps tenant A's
    owner from committing tenant B's bill, and the line that keeps tenant A's query
    from retrieving tenant B's chunks.

If you can't answer any of these without looking, the phase is not done.

## Ready for Phase 6?

You are ready when:
- Every checkbox above is checked.
- All 10 defend-it questions can be answered fluently, out loud, without notes.
- `cd backend && uv run pytest tests` is green (incl. the OCR, bill-HIL, and RAG
  suites and the no-commit-without-token test); the frontend CI job and the worker
  image build are green.
- A live demo runs end-to-end: a real paper bill is photographed → OCR'd in the
  worker → reviewed → approved → stock increases → and a customer's policy question is
  answered from RAG — RTL, Lebanese Arabic, on a 360px screen.

Phase 6 is the ML Layer — real trained models for demand forecasting, churn, and
revenue anomalies. It uses the historical-bill corpus this phase built (for
forecasting context) and the inventory movements Phases 4–5 produced. Do not start it
until the OCR + KB pipeline is solid and demoable end-to-end.
