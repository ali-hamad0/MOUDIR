"""Phase 10 — push-to-talk voice chat (dashboard).

Covers:
- wrap_pcm_in_wav produces a valid RIFF/WAVE container.
- SpeechSynthesizer dev mode returns a playable silent WAV without network.
- The /chat/sessions/{id}/voice endpoint end to end with stubbed audio
  components: transcript becomes the owner bubble, the reply is persisted and
  returned spoken (base64 WAV). Routing itself is proven elsewhere.
"""

import base64
import struct
from datetime import date, timedelta
from io import BytesIO
from types import SimpleNamespace

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.api.chat import send_voice_message
from app.infra.settings import Settings
from app.infra.tts import SpeechSynthesizer, wrap_pcm_in_wav
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository
from app.services.chat import ChatService
from tests.conftest import TwoTenants


async def _make_pro(db: AsyncSession, tenant_id) -> None:
    """Voice chat is Pro-only (Phase 11 plan gate) — upgrade the test tenant."""
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    assert tenant is not None
    tenant.plan_tier = "pro"
    tenant.subscription_status = "active"
    tenant.current_period_end = date.today() + timedelta(days=30)


def _settings(mode: str = "dev") -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        redis_url="redis://localhost:6379",
        vault_addr="http://localhost:8200",
        vault_token="root",
        whatsapp_mode=mode,
    )


def test_wrap_pcm_in_wav_header():
    pcm = b"\x01\x02" * 100
    wav = wrap_pcm_in_wav(pcm, sample_rate=24000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    # RIFF size = 36 + data length; data chunk length at offset 40.
    assert struct.unpack("<I", wav[4:8])[0] == 36 + len(pcm)
    assert struct.unpack("<I", wav[40:44])[0] == len(pcm)
    assert wav[44:] == pcm


async def test_synthesizer_dev_mode_returns_silent_wav():
    wav, mime = await SpeechSynthesizer(_settings("dev")).synthesize("مرحبا")
    assert mime == "audio/wav"
    assert wav[:4] == b"RIFF"


class _StubTranscriber:
    async def transcribe(self, audio_bytes, mime_type):
        assert audio_bytes == b"FAKE_OGG"
        return "شو ناقص من المخزون؟"


class _StubSynthesizer:
    async def synthesize(self, text):
        return wrap_pcm_in_wav(b"\x00\x00" * 10), "audio/wav"


class _StubSupervisor:
    async def handle(self, message, tenant_id, session_id):
        return "مخزونك تمام!"


class _StubCheckpointer:
    async def aget_tuple(self, config):
        return SimpleNamespace(checkpoint={"channel_values": {"routed_to": "inventory"}})


async def test_voice_turn_persists_and_speaks(db_session: AsyncSession, two_tenants: TwoTenants):
    a = two_tenants.a
    await _make_pro(db_session, a.tenant_id)
    user = await UserRepository(db_session).get_by_email(a.tenant_id, a.user_email)
    chat = await ChatService(db_session).create_session(tenant_id=a.tenant_id, user_id=user.id)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                audio_transcriber=_StubTranscriber(),
                speech_synthesizer=_StubSynthesizer(),
                supervisor=_StubSupervisor(),
                checkpointer=_StubCheckpointer(),
            )
        )
    )
    upload = UploadFile(
        file=BytesIO(b"FAKE_OGG"),
        filename="turn.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )

    out = await send_voice_message(
        session_id=chat.id,
        audio=upload,
        current_user=user,
        db=db_session,
        request=request,
    )

    assert out.transcript == "شو ناقص من المخزون؟"
    assert out.response == "مخزونك تمام!"
    assert out.agent == "inventory"
    assert base64.b64decode(out.audio_b64)[:4] == b"RIFF"

    # The voice turn reads like any other turn in the saved history.
    messages = await ChatService(db_session).list_messages(
        tenant_id=a.tenant_id, user_id=user.id, session_id=chat.id
    )
    assert [(m.role, m.content) for m in messages] == [
        ("owner", "شو ناقص من المخزون؟"),
        ("modir", "مخزونك تمام!"),
    ]
