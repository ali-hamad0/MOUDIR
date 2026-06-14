from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. All values typed and validated at startup.

    Required env vars: DATABASE_URL, REDIS_URL, VAULT_ADDR, VAULT_TOKEN
    """

    # Environment
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # Database
    database_url: PostgresDsn

    # Redis
    redis_url: RedisDsn

    # Vault (secrets resolution)
    vault_addr: str
    vault_token: SecretStr

    # MinIO — supplier-bill image storage (Phase 5). Endpoint + creds; the creds
    # resolve from Vault (modir/minio). The bucket holds bill images under
    # tenant-prefixed keys (the Wall for object storage — see app/infra/storage.py).
    minio_endpoint: str = Field(default="minio:9000")
    # The endpoint BROWSERS use for presigned URLs (e.g. "localhost:9000" in dev —
    # "minio:9000" only resolves inside the compose network). Presigned URLs are
    # signature-bound to the host, so the URL must be SIGNED for this endpoint,
    # not rewritten after. Empty → fall back to minio_endpoint (in-network use).
    minio_public_endpoint: str = Field(default="")
    minio_access_key: SecretStr = Field(default=SecretStr("changeme"))
    minio_secret_key: SecretStr = Field(default=SecretStr("changeme"))
    minio_bucket: str = Field(default="modir-bills")
    # TLS to MinIO. Off in dev (the compose MinIO speaks plain HTTP on the internal
    # network); a real deployment terminates TLS and sets this True.
    minio_secure: bool = Field(default=False)

    # Supplier-bill upload limits (Phase 5). A bill is a phone photo; cap the size
    # so a hostile or accidental huge upload can't exhaust memory/storage, and
    # restrict to image types the OCR engine can read. Enforced at the upload route.
    bill_upload_max_bytes: int = Field(default=10 * 1024 * 1024)  # 10 MiB
    bill_upload_allowed_content_types: list[str] = Field(
        default=["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]
    )
    # TTL for the presigned image URL the review screen uses (Task 5.12).
    bill_image_url_ttl_minutes: int = Field(default=15)

    # Billing (Phase 11, manual subscriptions). Non-secret config: how owners pay
    # the founder out-of-band. billing_whish_link is a static Whish Money pay
    # link/QR URL (the simplest real payment rail in Lebanon — no API needed);
    # billing_contact_phone is the founder's WhatsApp for renewals/upgrades.
    # Both optional: empty → the dashboard hides the corresponding action. A
    # gateway API (Whish for Business / a card processor) plugs in later behind
    # this same config seam without touching application code.
    billing_whish_link: str = Field(default="")
    billing_contact_phone: str = Field(default="")

    # Whish Pay collect API (Phase 11) — online subscription checkout.
    # "off" (default): the in-app checkout is disabled END TO END — the API
    # refuses to start one, and the dashboard's subscribe button links to the
    # static BILLING_WHISH_LINK instead (owner pays manually, sends the receipt
    # on WhatsApp, the founder records the payment). The safe state while no
    # merchant credentials exist: a simulated checkout must never grant Pro in
    # a real deployment.
    # "dev" never calls the network: the checkout completes locally with a
    # loudly-logged SIMULATED success (CI/tests only). "live" needs the
    # merchant channel + secret Whish issues after onboarding
    # (https://apps.whish.money) — secrets resolve from Vault (modir/whish),
    # never from code. Endpoint paths are config so a spec revision is a config
    # fix, not a code change.
    whish_pay_mode: str = Field(default="off")  # "off" | "dev" | "live"
    whish_pay_base_url: str = Field(default="https://lb.sandbox.whish.money/itel-service/api")
    whish_pay_create_path: str = Field(default="payment/whish")
    whish_pay_status_path: str = Field(default="payment/collect/status")
    whish_pay_channel: SecretStr = Field(default=SecretStr(""))
    whish_pay_secret: SecretStr = Field(default=SecretStr(""))
    whish_pay_website_url: str = Field(default="http://localhost:5173")
    # Where Whish redirects the payer after the hosted page (the dashboard's
    # result route, which then VERIFIES server-side before showing success).
    billing_result_base_url: str = Field(default="http://localhost:5173/billing/result")

    # OCR engine (Phase 5). Provider-agnostic like mail_mode / po_dispatch_mode:
    # "stub" returns deterministic canned text for CI/tests (offline, no network,
    # no key — the default); "cloud_vision" calls Google Cloud Vision (better
    # Lebanese-Arabic accuracy) and is verified on the HOST (Docker DNS is blocked
    # in-container). "gemini" OCRs via the existing Gemini key (no GCP service
    # account needed). "tesseract" is reserved as a documented offline fallback.
    ocr_mode: str = Field(default="stub")  # "stub" | "cloud_vision" | "gemini" | "tesseract"
    # A per-field confidence (0..1) at or below this is FLAGGED for closer review in
    # the UI (Task 5.6/5.12). It is a review SIGNAL, never an auto-commit switch:
    # every bill goes to a human in Phase 5 regardless of confidence.
    ocr_confidence_review_threshold: float = Field(default=0.75)

    # Background worker (Phase 5, Task 5.8). How often the OCR worker polls for
    # claimable bills (and, later, pending embeddings), and how many it claims per
    # tenant per pass. Poll-based for now; Phase 8 may move to a durable queue.
    worker_poll_seconds: float = Field(default=5.0)
    worker_batch_size: int = Field(default=10)

    # Embeddings + RAG (Phase 5). Provider-agnostic like the OCR/mail seams:
    # "stub" returns deterministic hashed pseudo-vectors for CI/tests (offline, no
    # network, no key — the default); "gemini" calls Google's embedding model (keyed
    # by the existing gemini_api_key from Vault — no new secret). The dimension MUST
    # match the model: text-embedding-004 is 768-d, and it must equal the pgvector
    # column width (a migration is needed to change it).
    embedding_mode: str = Field(default="stub")  # "stub" | "gemini"
    embedding_model: str = Field(default="models/text-embedding-004")
    embedding_dim: int = Field(default=768)
    # GCP service-account JSON for Cloud Vision — RESOLVED FROM VAULT (modir/ocr),
    # not env. The whole service-account credential is stored as one JSON string and
    # the Vision client is built from it (json.loads → from_service_account_info).
    # Empty default means "no credential" (stub/dev); when cloud_vision mode is used
    # the seed provides it. Keep in sync: vault.py secrets_map AND both seed paths.
    ocr_service_account_json: SecretStr = Field(default=SecretStr(""))

    # ML layer (Phase 6). Provider-agnostic like the OCR/embedding seams:
    # "stub" returns deterministic, offline predictions needing no trained artifact —
    # the CI/dev DEFAULT, so the suite runs offline with no .joblib committed; "trained"
    # loads the real pipelines from app/ml/artifacts/ at startup (lifespan, once) and
    # serves them through DI. A missing artifact under "trained" degrades to the stub
    # (logged), never crashes the api. Artifact FILENAMES are config so a retrain can
    # version them without touching code; they resolve under app/ml/artifacts/.
    ml_mode: str = Field(default="stub")  # "stub" | "trained"
    ml_demand_artifact: str = Field(default="demand.joblib")
    ml_churn_artifact: str = Field(default="churn.joblib")
    ml_anomaly_artifact: str = Field(default="anomaly.joblib")

    # ASR / voice transcription (Phase 12). Provider-agnostic exactly like ocr_mode /
    # ml_mode: transcribe_mode selects the backend behind the SAME
    # AudioTranscriber.transcribe(...) seam, so no webhook caller changes.
    #   "dev"     — canned Arabic stub (offline, no network, no key) — the CI/dev DEFAULT.
    #   "gemini"  — the Phase-10 Gemini native-audio REST path; the zero-artifact LIVE
    #               fallback so WhatsApp voice keeps working with no model fetched.
    #   "whisper" — the fine-tuned whisper-small, loaded ONCE in lifespan (Task 12.4).
    # The heavy torch/transformers stack is quarantined to the "whisper" branch and
    # lazily imported there only (AD-12.6); "dev"/"gemini" never touch it.
    transcribe_mode: str = Field(default="dev")  # "dev" | "gemini" | "whisper"
    # Where the fine-tuned artifact (~1 GB, git-ignored — AD-12.4) is fetched FROM: a
    # GitHub Release asset, a Hugging Face repo id, or a MinIO/S3 URI. Empty in CI/dev;
    # a documented fetch step pulls it to whisper_model_path before "whisper" is used.
    whisper_model_uri: str = Field(default="")
    # Local cache the WhisperTranscriber loads the model + processor from. Under
    # app/asr/artifacts/ by default (git-ignored). CI/dev never need it.
    whisper_model_path: str = Field(default="app/asr/artifacts/whisper-small-ar")
    # Inference device for the loaded model. "cpu" by default — a single short voice
    # note transcribes acceptably on CPU at serve time; a GPU host can set "cuda".
    whisper_device: str = Field(default="cpu")  # "cpu" | "cuda"

    # Dashboard CORS — the React app (Phase 3) runs on a different origin, so the
    # browser needs explicit cross-origin permission. A typed list, never "*"
    # with credentials (that combination is a security hole the browser itself
    # rejects). Default is the Vite dev server. Override in .env with a
    # JSON array, e.g. CORS_ALLOW_ORIGINS=["https://app.modir.example"].
    cors_allow_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # LLM provider keys — RESOLVED FROM VAULT, not env. Placeholder here.
    # Gemini is required; Grok and Anthropic are optional fallbacks (empty = skipped).
    gemini_api_key: SecretStr = Field(default=SecretStr("from-vault"))
    # Phase 7 — fallback providers. Empty string = provider not configured; the
    # FallbackLLMRouter skips providers with no key rather than crashing startup.
    grok_api_key: SecretStr = Field(default=SecretStr(""))
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))

    # LLM model selection (non-secret config). Parsing is Tier 1 work — Flash,
    # not Pro (cheaper, fast enough; see ROADMAP Phase 2 pitfall).
    llm_tier1_model: str = Field(default="gemini-2.5-flash")
    llm_tier2_model: str = Field(default="gemini-2.5-pro")
    llm_max_retries: int = Field(default=2)  # bad tool output → retry, not crash
    # Phase 7 fallback provider models. Tier 1 = fast/cheap; Tier 2 = stronger.
    grok_tier1_model: str = Field(default="grok-3-mini")
    grok_tier2_model: str = Field(default="grok-3")
    anthropic_tier1_model: str = Field(default="claude-haiku-4-5-20251001")
    anthropic_tier2_model: str = Field(default="claude-sonnet-4-6")

    # LangSmith tracing — key RESOLVED FROM VAULT, not env. Placeholder here.
    langsmith_api_key: SecretStr = Field(default=SecretStr("from-vault"))
    langsmith_project: str = Field(default="modir-phase2")
    langsmith_tracing: bool = Field(default=True)

    # JWT auth — secret RESOLVED FROM VAULT, not env. Placeholder here.
    jwt_secret: SecretStr = Field(default=SecretStr("from-vault"))
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiry_minutes: int = Field(default=15)
    # Refresh tokens are documented but not implemented in Phase 1 (see DoD).
    jwt_refresh_expiry_minutes: int = Field(default=60 * 24 * 7)
    # Signed approval tokens (the HIL execution gate, Phase 4 — ActionGate).
    # A human's approval is time-boxed: a minted approval token authorizes its
    # one action until this TTL elapses, so a leaked or stale approval cannot be
    # replayed indefinitely. Reuses jwt_secret — no new secret to seed in Vault.
    approval_token_ttl_minutes: int = Field(default=30)

    # Email (founder onboarding, Phase 1.5). Non-secret config here; the provider
    # API key is RESOLVED FROM VAULT. mail_mode "dev" sends to MailHog over SMTP
    # (or logs) and NEVER sends real mail; "api" would POST to a provider.
    mail_mode: str = Field(default="dev")  # "dev" (MailHog/log) | "api" (provider)
    mail_from: str = Field(default="Modir <no-reply@modir.local>")
    mail_smtp_host: str = Field(default="mailhog")  # the MailHog service name
    mail_smtp_port: int = Field(default=1025)
    # Activation links point at the dashboard; the owner clicks to set a password.
    activation_base_url: str = Field(default="http://localhost:5173/activate")
    activation_ttl_minutes: int = Field(default=60 * 24)  # one-time link valid 24h
    # Provider API key — RESOLVED FROM VAULT, not env. Placeholder here.
    mail_api_key: SecretStr = Field(default=SecretStr("from-vault"))

    # Supplier dispatch (the HIL send leg, Phase 4 — SupplierDispatcher). Mirrors
    # the EmailSender shape: "dev" logs the PO payload (or posts to MailHog) and
    # NEVER calls a real external supplier; "webhook" POSTs to the supplier's
    # webhook_url via httpx. Dispatch only runs AFTER the signed approval token
    # clears the ActionGate (constitution V) — these settings tune HOW it sends,
    # never WHETHER it is authorized to.
    po_dispatch_mode: str = Field(default="dev")  # "dev" (log/MailHog) | "webhook"
    # Retry budget for a transient supplier failure. After this many attempts the
    # PO is marked dispatch_failed and surfaced in the manual queue (Task 4.12) —
    # the row is the source of truth, so a missed send is always recoverable.
    po_dispatch_max_retries: int = Field(default=3)
    # Base delay for exponential backoff between attempts (delay = base * 2**n).
    po_dispatch_backoff_seconds: float = Field(default=1.0)
    # Optional shared secret sent as a webhook auth header (X-Modir-Signature),
    # so the supplier can verify the call really came from Modir. RESOLVED FROM
    # VAULT, not env — placeholder here. Empty default means "no auth header"
    # (dev / a supplier that does not require one); when a real shared secret is
    # introduced, add it to vault.py secrets_map AND both seed paths.
    po_dispatch_webhook_secret: SecretStr = Field(default=SecretStr(""))

    # WhatsApp Business API — Phase 10. Non-secret config here; the two secrets
    # (api_token, verify_token) are RESOLVED FROM VAULT under modir/whatsapp.
    # whatsapp_mode mirrors mail_mode / ocr_mode:
    #   "dev"  — log outbound messages, never call the real Meta API (CI default)
    #   "live" — POST to graph.facebook.com (requires a valid Meta app + approval)
    whatsapp_mode: str = Field(default="dev")  # "dev" | "live"
    # Meta's internal ID for the business phone number (NOT the human-readable
    # number; that lives in tenants.whatsapp_number). This is an identifier, not
    # a secret — set via WHATSAPP_PHONE_NUMBER_ID in .env. Dev can leave it empty
    # (the client logs in dev mode and never uses this value for API calls).
    whatsapp_phone_number_id: str = Field(default="")
    # Secrets — RESOLVED FROM VAULT, not env. Placeholders here.
    # api_token: the Meta permanent system-user access token (bearer header)
    # verify_token: the random string registered in the Meta App Dashboard to prove
    #   you control the webhook URL (checked on GET /webhooks/whatsapp)
    whatsapp_api_token: SecretStr = Field(default=SecretStr("from-vault"))
    whatsapp_verify_token: SecretStr = Field(default=SecretStr("from-vault"))

    # Rate limiting (Phase 8, Task 8.1). Default requests-per-minute applied to
    # customer-facing endpoints when the tenant has no `rate_limit_rpm` policy row.
    # A per-tenant policy value of "0" means no limit (explicit bypass).
    rate_limit_default_rpm: int = Field(default=30)

    # Signup phone OTP — the WhatsApp one-time code that verifies the phone on a
    # public registration request BEFORE it is accepted. State lives in Redis only
    # (no DB row; there is no tenant yet). The caps below make the endpoint unusable
    # as a WhatsApp-spam/bombing tool: a short cooldown between sends, a per-phone
    # hourly send cap, and a per-code attempt cap that burns the code once exceeded.
    signup_otp_length: int = Field(default=6)
    signup_otp_ttl_seconds: int = Field(default=300)  # code valid 5 minutes
    signup_otp_resend_cooldown_seconds: int = Field(default=60)
    signup_otp_max_sends_per_hour: int = Field(default=5)
    signup_otp_max_attempts: int = Field(default=5)

    # Log aggregator (Phase 8, Task 8.6). Empty string disables Loki shipping.
    # Set to http://loki:3100 when running with --profile observability.
    loki_url: str = Field(default="")

    # Paths
    base_dir: Path = Field(default=Path(__file__).parent.parent.parent)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )


@lru_cache
def get_settings() -> Settings:
    """Singleton accessor. Used via FastAPI Depends() in routes."""
    return Settings()
