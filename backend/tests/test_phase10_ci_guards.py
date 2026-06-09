"""Phase 10 CI guard tests (Task 10.6).

Structural smoke-tests that verify every Phase 10 deliverable exists and meets
its minimal contract.  No DB, no network, no Vault — all assertions are import
checks, file-system checks, or lightweight in-process calls.  These run in the
normal pytest suite and block the branch from merging if any deliverable is
missing or broken.
"""

from __future__ import annotations

import inspect
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

# ── 1. Settings fields ────────────────────────────────────────────────────────


def test_settings_has_whatsapp_mode():
    """Settings must expose whatsapp_mode as a typed field."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("VAULT_ADDR", "http://localhost:8200")
    os.environ.setdefault("VAULT_TOKEN", "root")
    from app.infra.settings import Settings

    assert (
        hasattr(Settings.model_fields, "whatsapp_mode") or "whatsapp_mode" in Settings.model_fields
    )


def test_whatsapp_mode_defaults_to_dev():
    """whatsapp_mode must default to 'dev' — CI must never call the live Meta API."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("VAULT_ADDR", "http://localhost:8200")
    os.environ.setdefault("VAULT_TOKEN", "root")
    from app.infra.settings import Settings

    s = Settings()
    assert s.whatsapp_mode == "dev", f"Expected 'dev', got {s.whatsapp_mode!r}"


def test_settings_has_whatsapp_secret_fields():
    """Settings must have api_token and verify_token as SecretStr fields."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("VAULT_ADDR", "http://localhost:8200")
    os.environ.setdefault("VAULT_TOKEN", "root")
    from app.infra.settings import Settings

    fields = Settings.model_fields
    assert "whatsapp_api_token" in fields, "whatsapp_api_token missing from Settings"
    assert "whatsapp_verify_token" in fields, "whatsapp_verify_token missing from Settings"
    assert "whatsapp_phone_number_id" in fields, "whatsapp_phone_number_id missing from Settings"


# ── 2. Meta payload schema ────────────────────────────────────────────────────


def test_meta_webhook_schema_importable():
    """MetaWebhookPayload must be importable from the schemas package."""
    from app.api.schemas.meta_webhook import MetaWebhookPayload  # noqa: F401


def test_extract_message_text():
    """A text-message Meta payload must parse to a correct InboundMessage."""
    from app.api.schemas.meta_webhook import MetaWebhookPayload
    from app.infra.whatsapp_parser import extract_message

    payload = MetaWebhookPayload.model_validate(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15556544382",
                                    "phone_number_id": "1237405232778967",
                                },
                                "contacts": [{"profile": {"name": "Ali"}, "wa_id": "96170999999"}],
                                "messages": [
                                    {
                                        "id": "wamid.GUARD10",
                                        "from": "96170999999",
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": "بدي طلب"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    )
    msg = extract_message(payload)
    assert msg is not None
    assert msg.to == "+15556544382"
    assert msg.from_ == "+96170999999"
    assert msg.text == "بدي طلب"
    assert msg.audio_id is None
    assert msg.display_name == "Ali"
    assert msg.message_type == "text"


def test_extract_message_audio():
    """An audio-message Meta payload must parse with audio_id set and text None."""
    from app.api.schemas.meta_webhook import MetaWebhookPayload
    from app.infra.whatsapp_parser import extract_message

    payload = MetaWebhookPayload.model_validate(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15556544382",
                                    "phone_number_id": "1237405232778967",
                                },
                                "contacts": [],
                                "messages": [
                                    {
                                        "id": "wamid.AUDIO10",
                                        "from": "96170888888",
                                        "timestamp": "1700000001",
                                        "type": "audio",
                                        "audio": {
                                            "id": "MEDIA_ID_GUARD",
                                            "mime_type": "audio/ogg; codecs=opus",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    )
    msg = extract_message(payload)
    assert msg is not None
    assert msg.audio_id == "MEDIA_ID_GUARD"
    assert msg.text is None
    assert msg.message_type == "audio"
    assert msg.from_ == "+96170888888"


def test_extract_message_status_update_returns_none():
    """A delivery-receipt / status-update payload must return None (silent 200)."""
    from app.api.schemas.meta_webhook import MetaWebhookPayload
    from app.infra.whatsapp_parser import extract_message

    payload = MetaWebhookPayload.model_validate(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15556544382",
                                    "phone_number_id": "1237405232778967",
                                },
                                "statuses": [
                                    {
                                        "id": "wamid.STATUS",
                                        "status": "delivered",
                                        "timestamp": "1700000002",
                                        "recipient_id": "96170999999",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    )
    assert extract_message(payload) is None, "Status update must return None"


# ── 3. WhatsApp client ────────────────────────────────────────────────────────


def test_whatsapp_client_importable():
    """WhatsAppClient must be importable and have send_text + download_media."""
    from app.infra.whatsapp import WhatsAppClient

    assert callable(getattr(WhatsAppClient, "send_text", None))
    assert callable(getattr(WhatsAppClient, "download_media", None))


# ── 4. Audio transcriber ──────────────────────────────────────────────────────


def test_audio_transcriber_importable():
    """AudioTranscriber must be importable and have a transcribe method."""
    from app.infra.audio_transcriber import AudioTranscriber

    assert callable(getattr(AudioTranscriber, "transcribe", None))


def test_prompts_audio_ar_has_required_constants():
    """prompts/audio_ar.py must export TRANSCRIPTION_SYSTEM and DEV_STUB."""
    from prompts.audio_ar import DEV_STUB, TRANSCRIPTION_SYSTEM

    assert TRANSCRIPTION_SYSTEM, "TRANSCRIPTION_SYSTEM must not be empty"
    assert DEV_STUB, "DEV_STUB must not be empty"


# ── 5. GET verification endpoint ─────────────────────────────────────────────


def _make_verify_app(verify_token: str) -> FastAPI:
    """Minimal FastAPI with the webhooks router and a fake verify_token setting."""
    from app.api import webhooks
    from app.infra.settings import get_settings

    class _FakeSettings:
        whatsapp_verify_token = SecretStr(verify_token)

    app = FastAPI()
    app.include_router(webhooks.router)
    app.dependency_overrides[get_settings] = lambda: _FakeSettings()
    return app


def test_get_verification_wrong_token_returns_403():
    """GET /webhooks/whatsapp with wrong token must return 403."""
    app = _make_verify_app("correct-token")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG",
            "hub.challenge": "99999",
        },
    )
    assert r.status_code == 403, f"Expected 403 on wrong token, got {r.status_code}"


def test_get_verification_correct_token_returns_challenge():
    """GET /webhooks/whatsapp with correct token must return 200 + challenge body."""
    app = _make_verify_app("correct-token")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "correct-token",
            "hub.challenge": "87654321",
        },
    )
    assert r.status_code == 200, f"Expected 200 on correct token, got {r.status_code}"
    assert r.text == "87654321", f"Expected challenge body '87654321', got {r.text!r}"


# ── 6. Vault path guard ───────────────────────────────────────────────────────


def test_vault_whatsapp_path_present_in_resolve_secrets():
    """vault.py must reference modir/whatsapp — otherwise secrets never resolve."""
    from app.infra import vault

    src = inspect.getsource(vault)
    assert "modir/whatsapp" in src, "vault.py does not seed modir/whatsapp secrets"
