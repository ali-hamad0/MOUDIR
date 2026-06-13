"""Offline tests for the ASR WER golden eval (Task 12.6).

The threshold + golden loaders and the artifact-absent SKIP path are pure file/logic, so
they run in normal CI with no torch and no network. The real WER scoring (model + gated
Common Voice download) is a host/Colab step and is not exercised here.
"""

from __future__ import annotations

import subprocess
import sys

from app.asr.eval import evaluate_golden, load_golden, load_thresholds


def test_thresholds_has_asr_wer_max():
    th = load_thresholds()
    assert "asr" in th
    assert isinstance(th["asr"]["wer_max"], (int, float))


def test_golden_set_is_validation_indices():
    g = load_golden()
    assert g["split"] == "validation"
    assert g["dataset"].startswith("mozilla-foundation/common_voice")
    assert isinstance(g["indices"], list) and len(g["indices"]) > 0
    assert all(isinstance(i, int) for i in g["indices"])


def test_evaluate_golden_skips_when_artifact_absent():
    # No artifact on disk → SKIP (passed), no heavy imports, no raise.
    result = evaluate_golden("app/asr/artifacts/__does_not_exist__")
    assert result.skipped is True
    assert result.passed is True
    assert "wer_max" in result.thresholds


def test_main_skips_and_exits_zero_without_torch():
    """`python -m app.asr.eval` with no artifact must SKIP, exit 0, and import no torch.
    Run in a fresh interpreter so the torch-absence check is deterministic."""
    code = (
        "import sys;"
        "from app.asr.eval import main;"
        "rc = main();"
        "assert 'torch' not in sys.modules, 'torch imported on the skip path';"
        "assert rc == 0, rc;"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
