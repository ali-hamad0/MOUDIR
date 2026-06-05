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

    # MinIO (later phases)
    minio_endpoint: str = Field(default="minio:9000")
    minio_access_key: SecretStr = Field(default=SecretStr("changeme"))
    minio_secret_key: SecretStr = Field(default=SecretStr("changeme"))

    # Dashboard CORS — the React app (Phase 3) runs on a different origin, so the
    # browser needs explicit cross-origin permission. A typed list, never "*"
    # with credentials (that combination is a security hole the browser itself
    # rejects). Default is the Vite dev server. Override in .env with a
    # JSON array, e.g. CORS_ALLOW_ORIGINS=["https://app.modir.example"].
    cors_allow_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # LLM provider keys — RESOLVED FROM VAULT, not env. Placeholder here.
    gemini_api_key: SecretStr = Field(default=SecretStr("from-vault"))

    # LLM model selection (non-secret config). Parsing is Tier 1 work — Flash,
    # not Pro (cheaper, fast enough; see ROADMAP Phase 2 pitfall).
    llm_tier1_model: str = Field(default="gemini-2.5-flash")
    llm_tier2_model: str = Field(default="gemini-2.5-pro")
    llm_max_retries: int = Field(default=2)  # bad tool output → retry, not crash

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
