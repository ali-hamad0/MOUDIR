"""Task 12.4 — transcriber factory selection + the whisper→gemini fallback.

These cover dev + gemini selection WITHOUT importing torch (AD-12.6). The whisper
backend itself is only smoke-imported behind a skip when the `asr` extra is absent; a
real OGG transcription needs the artifact and is a host/Colab check.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys

import httpx
import pytest

from app.infra.audio_transcriber import (
    DevAudioTranscriber,
    GeminiAudioTranscriber,
    build_audio_transcriber,
)
from app.infra.settings import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        redis_url="redis://localhost:6379",
        vault_addr="http://localhost:8200",
        vault_token="root",
    )
    base.update(overrides)
    return Settings(**base)


def test_dev_mode_selects_stub_backend():
    t = build_audio_transcriber(_settings(transcribe_mode="dev"))
    assert isinstance(t, DevAudioTranscriber)


async def test_dev_backend_returns_the_arabic_stub():
    from prompts.audio_ar import DEV_STUB

    out = await DevAudioTranscriber().transcribe(b"FAKE_OGG", "audio/ogg")
    assert out == DEV_STUB


def test_gemini_mode_selects_gemini_backend():
    t = build_audio_transcriber(_settings(transcribe_mode="gemini"))
    assert isinstance(t, GeminiAudioTranscriber)


def test_whisper_mode_missing_artifact_falls_back_to_gemini():
    # No artifact on disk → degrade to gemini (logged), never crash (defend-it answer).
    t = build_audio_transcriber(
        _settings(
            transcribe_mode="whisper",
            whisper_model_path="app/asr/artifacts/__does_not_exist__",
        )
    )
    assert isinstance(t, GeminiAudioTranscriber)


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="transcribe_mode"):
        build_audio_transcriber(_settings(transcribe_mode="bogus"))


def test_default_path_imports_no_torch():
    """AD-12.6 guard: building the dev/gemini transcriber must not import torch. Run in a
    FRESH interpreter so the check is deterministic even where the asr extra is installed.
    """
    code = (
        "import sys;"
        "from app.infra.audio_transcriber import build_audio_transcriber;"
        "from app.infra.settings import Settings;"
        "s=Settings(database_url='postgresql+asyncpg://x:x@localhost/x',"
        "redis_url='redis://localhost:6379',vault_addr='http://localhost:8200',"
        "vault_token='root',transcribe_mode='gemini');"
        "build_audio_transcriber(s);"
        "assert 'torch' not in sys.modules, 'torch imported on the gemini path';"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


@pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="asr extra not installed (torch); whisper serve is a host/Colab check",
)
def test_whisper_backend_importable_when_torch_present():
    from app.infra.asr.whisper_transcriber import WhisperTranscriber, load_whisper

    assert callable(load_whisper)
    assert callable(getattr(WhisperTranscriber, "transcribe", None))


# ── gemini path: the Phase-10 behavior is preserved byte-for-byte (Task 12.5) ─────────
# httpx is mocked, so these run offline. They lock the live contract: the right endpoint,
# model, system prompt, inline audio, response parsing, and error handling.


def _mock_httpx(monkeypatch, handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport))


async def test_gemini_path_posts_inline_audio_and_returns_text(monkeypatch):
    from prompts.audio_ar import TRANSCRIPTION_SYSTEM

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "  hello world  "}]}}]},
        )

    _mock_httpx(monkeypatch, handler)
    transcriber = build_audio_transcriber(_settings(transcribe_mode="gemini"))

    out = await transcriber.transcribe(b"OGGBYTES", "audio/ogg; codecs=opus")

    assert out == "hello world"  # whitespace stripped, as in Phase 10
    assert "generativelanguage.googleapis.com" in captured["url"]
    assert "gemini-2.5-flash" in captured["url"]
    parts = captured["body"]["contents"][0]["parts"]
    assert parts[0]["text"] == TRANSCRIPTION_SYSTEM
    assert parts[1]["inline_data"]["mime_type"] == "audio/ogg; codecs=opus"
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == b"OGGBYTES"


async def test_gemini_path_raises_on_http_error(monkeypatch):
    _mock_httpx(monkeypatch, lambda request: httpx.Response(500, json={}))
    transcriber = build_audio_transcriber(_settings(transcribe_mode="gemini"))
    with pytest.raises(httpx.HTTPStatusError):
        await transcriber.transcribe(b"x", "audio/ogg")


async def test_gemini_path_returns_stub_on_malformed_response(monkeypatch):
    from prompts.audio_ar import DEV_STUB

    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json={"candidates": []}))
    transcriber = build_audio_transcriber(_settings(transcribe_mode="gemini"))
    assert await transcriber.transcribe(b"x", "audio/ogg") == DEV_STUB
