# Phase 10 — Real Meta WhatsApp Integration

**Goal:** Replace the internal test webhook format with the real Meta WhatsApp
Business API: webhook verification challenge, real Meta payload parsing, replies
sent back via the Messages API, and voice-message transcription via Gemini native
audio.

**Why this phase exists here:** All nine prior phases used a simplified internal
webhook schema to avoid Meta approval friction during development. Phase 10 is
where that scaffolding comes off and real WhatsApp traffic flows. The phone
identity, tenant routing, agents, and rate limiting built in Phases 1–8 remain
completely unchanged — this phase only changes the transport layer.

---

## Architectural decisions

- **`whatsapp_mode: "dev" | "live"`** mirrors the pattern of `mail_mode`,
  `po_dispatch_mode`, and `ocr_mode`. In `"dev"` mode the client logs the
  outbound message payload and never calls the real Meta API. In `"live"` mode
  it POSTs to `graph.facebook.com/v19.0/{phone_number_id}/messages`. CI always
  runs in `"dev"` mode — this is enforced by the CI guard test.

- **Secrets in Vault, never in `.env`.**
  Three new secrets are seeded: `whatsapp_api_token` (the Meta permanent system
  user token), `whatsapp_verify_token` (the random string you register in the
  Meta App Dashboard to prove you control the webhook URL), and
  `whatsapp_phone_number_id` (the Meta-internal ID for the business phone number,
  not the human-readable number). All three live under `modir/whatsapp` in Vault.

- **`display_phone_number` is the tenant key, not `phone_number_id`.**
  Meta's `metadata.display_phone_number` is the human-readable number that
  matches `tenants.whatsapp_number`. `metadata.phone_number_id` is Meta's
  internal ID and is NOT stored in our DB. The tenant lookup uses
  `display_phone_number`, which is the same format registered at signup.

- **Always return `200 OK` to Meta immediately.**
  Meta retries any webhook call that doesn't get a 2xx within ~5 seconds. We
  return `200` after dispatching and sending — if the LLM is slow this can be an
  issue. Phase 11 will move processing to a background task. For now, synchronous
  is acceptable for the demo.

- **Rate-limit hit → 200 + no reply** (silent drop for the customer, not a 429
  to Meta). We never return 4xx/5xx to Meta — they'd interpret that as a server
  error and retry.

- **`WhatsAppWebhookPayload` stays as an internal DTO.** The identity resolver
  already accepts `to`, `from_`, `display_name` as explicit arguments. The
  route handler parses the Meta payload itself and calls `IdentityResolver.resolve()`
  directly rather than via the FastAPI dependency. The old dependency remains in
  `deps.py` unchanged for the dashboard/chat endpoints that still use our internal
  format.

- **Voice messages: OGG → Gemini native audio → text → agent.**
  The `WhatsAppClient` downloads the OGG from Meta's media API (using the
  `media_id` from the payload). The `AudioTranscriber` in `infra/` wraps a
  direct Gemini multimodal REST call (via `httpx.AsyncClient`) — NOT the SDK,
  which is the one provider-SDK exception: audio transcription does not go through
  the LangChain router because LangChain's Gemini wrapper does not support inline
  audio bytes reliably. The transcription prompt is in `prompts/` (not inline).
  Transcription mode follows the same "dev" flag: in dev mode the transcriber
  returns a canned Arabic string.

---

## Tasks

### Task 10.1 — Settings + Vault + GET verification endpoint

**Deliverables:**
- Add to `Settings`: `whatsapp_verify_token`, `whatsapp_phone_number_id`,
  `whatsapp_api_token`, `whatsapp_mode` (default `"dev"`)
- Add `modir/whatsapp` to `resolve_secrets` in `vault.py` (required: all three)
- Add GET `/webhooks/whatsapp` handler that:
  - Verifies `hub.mode == "subscribe"` and `hub.verify_token` matches
  - Returns `hub.challenge` as a plain-text `200` response on success
  - Returns `403` if the token doesn't match
- Update `.env.example` to document `WHATSAPP_MODE=dev`
- Seed Vault with dummy values at startup (dev script / docker-compose)

**Constitution checks:** No `os.getenv` outside Settings; secrets in Vault;
verify token is a `SecretStr` (never logged).

---

### Task 10.2 — Real Meta payload schema + parser

**Deliverables:**
- `app/api/schemas/meta_webhook.py` — nested Pydantic schemas matching Meta's
  actual webhook body: `MetaWebhookPayload`, `MetaEntry`, `MetaChange`,
  `MetaValue`, `MetaMessage`, `MetaAudio`, `MetaText`, `MetaMetadata`
- `app/infra/whatsapp_parser.py` — `extract_message(payload: MetaWebhookPayload)`
  returns an `InboundMessage` dataclass with fields:
  `to` (display_phone_number), `from_` (sender), `text` (body or None),
  `audio_id` (Meta media_id or None), `display_name`, `message_type`
- Handles non-message notifications (status updates, read receipts) by returning
  `None` (the handler returns `200` and exits)

**Constitution checks:** Pydantic validation on every field; bad payload is a
400 (FastAPI default); `audio_id` is never logged in full.

---

### Task 10.3 — WhatsApp reply client (`app/infra/whatsapp.py`)

**Deliverables:**
- `WhatsAppClient` class with:
  - `send_text(to: str, body: str) -> None` — dev: structured log; live: POST
    to `graph.facebook.com/v19.0/{phone_number_id}/messages`
  - `download_media(media_id: str) -> tuple[bytes, str]` — GETs the OGG from
    Meta's media API using the bearer token; returns `(bytes, mime_type)`
  - Uses `httpx.AsyncClient` (never `requests`)
  - `phone_number_id` and `api_token` come from `settings` (Vault-resolved)
  - All outbound calls carry a `bearer` header; no token is logged
- Wire into lifespan: `app.state.whatsapp_client = WhatsAppClient(settings)`
- `build_whatsapp_client(settings) -> WhatsAppClient` factory mirrors
  `build_ocr_engine` pattern

**Constitution checks:** `import requests` absent; `httpx.AsyncClient`;
token is `SecretStr` — `get_secret_value()` only at call time, never earlier.

---

### Task 10.4 — Wire reply into the webhook handler (full end-to-end)

**Deliverables:**
- Rewrite `POST /webhooks/whatsapp` to:
  1. Accept `MetaWebhookPayload` as the request body
  2. Call `extract_message()` — if `None`, return `200` immediately
  3. Resolve identity via `IdentityResolver(db).resolve(...)` directly
  4. Rate-limit check via `request.app.state.rate_limiter` — on limit: return
     `200` silently (no reply sent)
  5. `await dispatcher.dispatch(text, identity)`
  6. `await whatsapp_client.send_text(from_, reply)`
  7. Return `Response(status_code=200)` (plain 200, no body — Meta ignores body)
- The old `WhatsAppWebhookPayload` body dependency is gone from this route.
  The `resolve_message_identity` dependency in `deps.py` remains for other routes.
- Structured log on every inbound: tenant_id, role, message_type, wamid.

**Constitution checks:** The Wall holds — every identity resolution is
tenant-scoped; the rate limiter fires before dispatch; reply is always sent
after `dispatcher.dispatch()`, never before.

---

### Task 10.5 — Voice message support

**Deliverables:**
- `app/infra/audio_transcriber.py` — `AudioTranscriber` class:
  - `transcribe(audio_bytes: bytes, mime_type: str) -> str`
  - In `"dev"` mode (checked via `settings.whatsapp_mode`): returns the canned
    Lebanese Arabic stub `"(رسالة صوتية - النص التجريبي)"`
  - In `"live"` mode: POST to `generativelanguage.googleapis.com` via
    `httpx.AsyncClient`, passing base64 audio + the transcription prompt from
    `prompts/audio_ar.py`
  - Uses `settings.gemini_api_key.get_secret_value()` as the API key
- `prompts/audio_ar.py` — a single `TRANSCRIPTION_SYSTEM` constant with the
  Lebanese Arabic transcription instruction
- Wire into lifespan: `app.state.audio_transcriber = AudioTranscriber(settings)`
- In the webhook handler (Task 10.4): if `message.audio_id` and text is None,
  call `download_media()` then `transcribe()` and use result as text

**Constitution checks:** Prompt in `prompts/` file (not inline); `httpx` not
`requests`; `gemini_api_key` accessed via `get_secret_value()` at call time only;
audio bytes are not logged (size is logged, not content).

---

### Task 10.6 — CI guards (`test_phase10_ci_guards.py`)

**Deliverables:**
- Structural tests (no DB, no network):
  - `test_settings_has_whatsapp_fields` — Settings has `whatsapp_mode`,
    `whatsapp_verify_token`, `whatsapp_phone_number_id`, `whatsapp_api_token`
  - `test_whatsapp_mode_defaults_to_dev` — CI never accidentally calls live API
  - `test_meta_webhook_schema_exists` — import `MetaWebhookPayload` succeeds
  - `test_extract_message_text` — parse a fixture text payload → correct fields
  - `test_extract_message_audio` — parse a fixture audio payload → audio_id set
  - `test_extract_message_status_update` — status updates return None
  - `test_whatsapp_client_exists` — `WhatsAppClient` importable
  - `test_audio_transcriber_exists` — `AudioTranscriber` importable
  - `test_prompts_audio_ar_exists` — `prompts/audio_ar.py` has TRANSCRIPTION_SYSTEM
  - `test_get_webhook_verification` — GET `/webhooks/whatsapp` returns 403 on
    wrong token and 200 + challenge on correct token (TestClient, no DB)
  - `test_vault_whatsapp_path_in_resolve_secrets` — `vault.py` source mentions
    `modir/whatsapp`

---

## Definition of done

- [ ] GET `/webhooks/whatsapp` returns `hub.challenge` when the verify token
  matches; returns 403 otherwise.
- [ ] POST `/webhooks/whatsapp` accepts a real Meta text-message payload,
  resolves identity, dispatches, and calls `send_text()` (in dev mode: logs it).
- [ ] POST `/webhooks/whatsapp` accepts a real Meta audio payload, downloads the
  OGG, transcribes it (in dev mode: stub), and dispatches the text.
- [ ] POST `/webhooks/whatsapp` returns `200` for status-update notifications
  (no reply sent).
- [ ] `whatsapp_mode` defaults to `"dev"` — CI never calls the real Meta API.
- [ ] All 11 CI guard tests pass.
- [ ] `grep -rn "import requests" backend/app/` returns zero matches.
- [ ] `grep -rn "whatsapp_api_token" backend/app/` returns matches only in
  `settings.py` and `vault.py` (never hardcoded elsewhere).

## Common pitfalls

- Returning a non-200 to Meta — they retry and flood the endpoint.
- Using `phone_number_id` (Meta's internal ID) for the tenant lookup instead of
  `display_phone_number` (the human-readable number in `tenants.whatsapp_number`).
- Logging the full `api_token` or `audio_id` in structured logs. Log lengths/IDs,
  never values.
- Calling `requests` instead of `httpx`. CI fails on `import requests`.
- Putting the transcription prompt inline in the Python file instead of `prompts/`.
- Forgetting that status-update notifications (delivery receipts, read receipts)
  arrive on the same POST endpoint — they don't have a `messages` array and must
  be silently ignored.

## Defend-it questions

- A Meta webhook arrives. Walk me through the path from the raw HTTP request to
  the reply being sent back to the customer.
- Where does the Meta API token live? Show me the code that reads it.
- What happens when Meta sends a voice message? Walk through the full transcription
  path.
- What happens when Meta sends a status update (delivery receipt)? Does the system
  reply?
- How do you make sure CI never calls the real Meta API?
- What's `display_phone_number` vs `phone_number_id`? Which one do you use for
  the tenant lookup and why?
- A rate-limited customer sends a voice message. What does Modir send back?
