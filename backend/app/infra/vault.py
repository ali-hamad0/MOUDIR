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

    Called from lifespan startup. Refuses to proceed if any required secret is
    missing. Optional secrets (Grok, Anthropic) are skipped when absent — the
    FallbackLLMRouter will skip those providers at startup.
    """
    from pydantic import SecretStr

    vault = VaultClient()

    # Required secrets — startup fails if any of these are absent in Vault.
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
        # WhatsApp Business API secrets (Phase 10). All three are required at
        # startup even in dev mode (the stubs resolve cleanly; the mode flag guards
        # actual API calls). In "dev" mode the values are never used — only the
        # verify_token is checked by the GET handler against the incoming challenge.
        "whatsapp_api_token": ("modir/whatsapp", "api_token"),
        "whatsapp_verify_token": ("modir/whatsapp", "verify_token"),
    }
    for field, (path, key) in secrets_map.items():
        value = vault.read_secret(path, key)
        setattr(settings, field, SecretStr(value))

    # Optional secrets — absent in Vault means the provider is skipped at startup
    # (logged as info, not an error). Leaving the field at its empty-string default
    # is the signal to FallbackLLMRouter to exclude that provider.
    optional_secrets_map = {
        "grok_api_key": ("modir/llm", "grok_api_key"),
        "anthropic_api_key": ("modir/llm", "anthropic_api_key"),
        # Whish Pay merchant credentials (Phase 11). Optional: absent means the
        # checkout runs in dev (simulated) mode; required in practice once
        # WHISH_PAY_MODE=live.
        "whish_pay_channel": ("modir/whish", "channel"),
        "whish_pay_secret": ("modir/whish", "secret"),
    }
    resolved_optional = 0
    for field, (path, key) in optional_secrets_map.items():
        try:
            value = vault.read_secret(path, key)
            setattr(settings, field, SecretStr(value))
            resolved_optional += 1
        except Exception:
            log.info("vault.optional_secret.absent", field=field)

    log.info(
        "vault.secrets.resolved",
        required=len(secrets_map),
        optional=resolved_optional,
    )
    return settings
