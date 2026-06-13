"""Phase 12 ASR CI guards (Task 12.7).

Structural + fresh-interpreter import checks proving the phase's two hard invariants:
  1. ASR is OFFLINE by default — transcribe_mode defaults to "dev".
  2. torch NEVER loads on the default path — importing app.asr, the whisper backend
     module, or the audio_transcriber factory module pulls no torch (AD-12.6).
Plus: the WER floor and results.csv header exist. No DB, no network, no torch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

# backend/ — tests/ is directly under it.
BACKEND = Path(__file__).resolve().parent.parent


def _fresh_import_imports_no_torch(import_line: str) -> None:
    """Run `import_line` in a FRESH interpreter and assert torch was not imported."""
    code = (
        "import sys;"
        f"{import_line};"
        "assert 'torch' not in sys.modules, 'torch imported by: " + import_line + "';"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ── 1. Offline by default ─────────────────────────────────────────────────────


def test_transcribe_mode_defaults_to_dev():
    from app.infra.settings import Settings

    fields = Settings.model_fields
    assert "transcribe_mode" in fields
    assert fields["transcribe_mode"].default == "dev"


def test_settings_has_whisper_fields():
    from app.infra.settings import Settings

    fields = Settings.model_fields
    for name in ("whisper_model_uri", "whisper_model_path", "whisper_device"):
        assert name in fields, f"{name} missing from Settings"


# ── 2. torch never on the default path (AD-12.6) ──────────────────────────────


def test_importing_app_asr_imports_no_torch():
    _fresh_import_imports_no_torch(
        "import app.asr, app.asr.dataset, app.asr.features, app.asr.train, app.asr.eval"
    )


def test_importing_whisper_backend_module_imports_no_torch():
    _fresh_import_imports_no_torch("import app.infra.asr.whisper_transcriber")


def test_importing_audio_transcriber_imports_no_torch():
    _fresh_import_imports_no_torch("import app.infra.audio_transcriber")


# ── 3. Eval floor + experiment-log schema are committed ───────────────────────


def test_eval_thresholds_has_asr_wer_max():
    data = yaml.safe_load((BACKEND / "app/asr/eval_thresholds.yaml").read_text(encoding="utf-8"))
    assert "asr" in data
    assert isinstance(data["asr"]["wer_max"], (int, float))


def test_results_csv_has_committed_header():
    header = (BACKEND / "app/asr/results.csv").read_text(encoding="utf-8").splitlines()[0]
    cols = header.split(",")
    assert cols[:4] == ["timestamp", "model", "base_model", "dataset"]
    assert "wer" in cols and "cer" in cols and "data_source" in cols


def test_whisper_backend_exposes_transcribe_and_loader():
    from app.infra.asr.whisper_transcriber import WhisperTranscriber, load_whisper

    assert callable(load_whisper)
    assert callable(getattr(WhisperTranscriber, "transcribe", None))
