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
