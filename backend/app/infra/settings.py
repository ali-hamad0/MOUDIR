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

    # LLM provider keys — RESOLVED FROM VAULT, not env. Placeholder here.
    gemini_api_key: SecretStr = Field(default=SecretStr("from-vault"))

    # LLM model selection (non-secret config). Parsing is Tier 1 work — Flash,
    # not Pro (cheaper, fast enough; see ROADMAP Phase 2 pitfall).
    llm_tier1_model: str = Field(default="gemini-1.5-flash")
    llm_tier2_model: str = Field(default="gemini-1.5-pro")
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
