import hvac

from app.infra.logging import get_logger
from app.infra.settings import get_settings

log = get_logger(__name__)


class VaultClient:
    """Reads secrets from Vault KV v2. Used at app startup."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = hvac.Client(
            url=settings.vault_addr,
            token=settings.vault_token.get_secret_value(),
        )
        if not self._client.is_authenticated():
            raise RuntimeError("Vault authentication failed at startup")

    def read_secret(self, path: str, key: str) -> str:
        """Read a single key from a secret at the given path.

        path: 'modir/gemini' (without 'secret/data/' prefix)
        key: the field name inside the secret
        """
        try:
            response = self._client.secrets.kv.v2.read_secret_version(path=path)
            return response["data"]["data"][key]
        except Exception as e:
            log.error("vault.read.failed", path=path, key=key, error=str(e))
            raise


def resolve_secrets(settings):
    """Mutate the settings object with secrets fetched from Vault.

    Called from lifespan startup. Refuses to proceed if any secret is missing.
    """
    vault = VaultClient()
    secrets_map = {
        "gemini_api_key": ("modir/llm", "gemini_api_key"),
        "langsmith_api_key": ("modir/llm", "langsmith_api_key"),
        "minio_access_key": ("modir/minio", "access_key"),
        "minio_secret_key": ("modir/minio", "secret_key"),
        "jwt_secret": ("modir/auth", "jwt_secret"),
        "mail_api_key": ("modir/mail", "api_key"),
        # GCP service-account JSON for the OCR engine (Phase 5). Seeded as a
        # placeholder in dev (stub mode ignores it); a real credential is provided
        # only when ocr_mode=cloud_vision.
        "ocr_service_account_json": ("modir/ocr", "service_account_json"),
    }
    for field, (path, key) in secrets_map.items():
        from pydantic import SecretStr

        value = vault.read_secret(path, key)
        setattr(settings, field, SecretStr(value))
    log.info("vault.secrets.resolved", count=len(secrets_map))
    return settings
