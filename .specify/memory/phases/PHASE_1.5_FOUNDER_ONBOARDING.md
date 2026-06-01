# Phase 1.5 — Founder-Gated Onboarding (Approval + Activation)

> **Status: PLANNED.** Build AFTER Phase 1 is complete and merged (the Wall and
> identity layer must be solid first). This replaces Phase 1's self-service
> `POST /auth/register` with a request → founder-approval → activation flow.
>
> **Hand this file to Claude Code with:**
> "Read `.specify/memory/constitution.md`, `PHASE_1.md`, and this file.
> Implement Phase 1.5 task by task. Pause for approval after each task."

---

## Why this exists

Modir is invite-only at launch. A business owner cannot self-provision a live
shop. Instead:

1. The owner submits a **signup request** (business name, owner phone, email).
   They get NO account and CANNOT log in yet.
2. The request sits as `pending`. The **Modir founder** reviews it.
3. The founder **approves** (optionally after the owner pays the subscription
   out-of-band). Approval provisions the tenant and sends the owner an
   **activation email** with a one-time link.
4. The owner clicks the link and **sets their own password**. Only then can
   they log in.
5. The founder can also **reject** a request (with a reason).

This gives the founder full control over who gets in, keeps onboarding
high-touch, and never emails a plaintext password.

### Decisions locked in (from the product owner)

- **Access model:** request → founder approves → provision. NOT instant signup.
- **Credentials:** activation link (owner sets own password). NOT emailed
  passwords — emailing plaintext passwords is a security risk and is rejected.
- **Payment:** handled OUT OF BAND for now (founder confirms payment manually
  before approving). No billing integration in this phase. A real subscription/
  billing system is a later, separate effort.

---

## How this changes Phase 1

- `POST /auth/register` (self-service, instant login) from Task 1.9 is
  **removed or locked down**. It is replaced by `POST /signup-requests`
  (public) + admin approval. Decide during Task 1.5.1 whether to delete the
  route or keep it behind a feature flag / founder-only guard.
- Phase 1 ships **ten tenant-scoped tables**. This phase adds tables/identities
  that are deliberately **NOT tenant-scoped** because they sit ABOVE tenants:
  - `signup_requests` — pending applications (no tenant_id; a request has no
    tenant until approved).
  - The **founder/super-admin** identity — someone above all tenants. Phase 1's
    `users` are all tenant-bound; the founder is not. This is a new concept and
    must be designed carefully so it does NOT weaken The Wall (a founder
    bypassing tenant scoping is a Sev-1 risk — see constitution I).

> ⚠️ The Wall still holds. The founder-admin is the ONE identity allowed to act
> across tenants, and only through dedicated admin endpoints that are explicitly
> audited. Normal tenant-scoped repositories are never given a "skip scope" mode.

---

## New infrastructure required

- **Email sending.** The stack has no mailer yet. Add an email service
  (`app/infra/email.py`) using `httpx.AsyncClient` against a provider API
  (per constitution: no `import requests`; provider-agnostic where practical).
  Dev mode logs the email / writes to MailHog instead of sending. SMTP/API
  credentials resolve from **Vault** (constitution II), never `.env`.
- **Activation tokens.** One-time, expiring tokens for set-password. Either a
  column on `users` (`activation_token`, `activation_expires_at`,
  `activated_at`) or a small `activation_tokens` table. Tokens are random
  (`secrets.token_urlsafe`), single-use, and time-boxed.

---

## Proposed tasks (refine before building)

| Task | What | Branch |
|------|------|--------|
| 1.5.1 | `signup_requests` model + migration; lock down old `/auth/register` | `feature/MOD-1.5-signup-requests` |
| 1.5.2 | Founder/super-admin identity + auth (separate from tenant `users`) | `feature/MOD-1.5-founder-admin` |
| 1.5.3 | Public `POST /signup-requests` (creates pending request, no account) | `feature/MOD-1.5-request-endpoint` |
| 1.5.4 | Email infra (`app/infra/email.py`) + Vault creds + dev MailHog | `feature/MOD-1.5-email-infra` |
| 1.5.5 | Activation tokens + `POST /activate` (owner sets password) | `feature/MOD-1.5-activation` |
| 1.5.6 | Founder admin: list/approve/reject requests; approve provisions tenant (reuse `register_tenant`) + sends activation email | `feature/MOD-1.5-approval` |
| 1.5.7 | Audit every step (request, approve, reject, activate) via AuditService | `feature/MOD-1.5-audit` |
| 1.5.8 | Tests: request→approve→activate→login happy path; reject path; expired/used token; founder cannot leak across The Wall | `feature/MOD-1.5-tests` |

Each task: one branch, one PR, pause for approval — same as Phase 1.

---

## Reuse from Phase 1 (do NOT rebuild)

- `register_tenant()` (`app/services/signup.py`) — approval calls this to create
  the Tenant + owner + blank profile. It already does the transaction; the
  difference is WHO triggers it (founder, not the public) and that the user is
  created WITHOUT a usable password until activation.
- `TenantOwner` pending/verification flow (Task 1.11) — the owner phone still
  goes through its own verification; that is separate from dashboard activation.
- `AuditService` (Task 1.13) — every approval/rejection/activation is audited.
- `prompts/` — all owner-facing email/notification copy in Lebanese Arabic.

---

## Definition of Done

- [ ] A business owner CANNOT log in without a founder-approved, activated account.
- [ ] Public signup creates a `pending` request only — no tenant, no user, no login.
- [ ] The founder can list, approve, and reject requests; each is audit-logged.
- [ ] Approval provisions the tenant (via `register_tenant`) and emails a one-time
      activation link. No plaintext password is ever sent.
- [ ] The activation link is single-use and expires; a used/expired link is rejected.
- [ ] After activation the owner sets their own password and can log in.
- [ ] The founder-admin identity is separate from tenant `users` and CANNOT be
      used to read/write tenant data through normal repositories — The Wall holds.
      A test proves a founder cannot cross-tenant leak.
- [ ] Email credentials resolve from Vault; dev mode does not send real email.
- [ ] `grep -rn "os.getenv\|print(\|import requests" backend/app/` still returns nothing.

## Open questions to resolve before building

1. Founder identity: a row in `users` with a special role + null tenant_id, or a
   separate `admins` table? (Leaning: separate table — keeps `users` strictly
   tenant-bound and The Wall simpler to reason about.)
2. Email provider for production (SES / Resend / Postmark / SMTP)? Dev uses MailHog.
3. Should the old `/auth/register` be deleted outright or kept founder-only for
   the founder to create tenants directly without a request?
4. Does "pay the subscription" need even a minimal record (e.g. `paid_at` on the
   request) for the founder to track, or is it fully external for now?
