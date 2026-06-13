"""Offline unit tests for the ASR dataset prep (Task 12.2).

The text-normalization policy is pure logic with no heavy dependency, so it runs in the
normal CI suite (no torch, no `datasets`, no network). Arabic test strings are built
from explicit Unicode codepoints (\\u escapes) — tashkeel are combining marks that are
invisible and corrupt easily as source literals. The actual Common Voice download (gated
on HF + ~GBs) is a host/Colab step, so the loader is only smoke-checked behind a skip
when the `asr` extra is absent.
"""

from __future__ import annotations

import importlib.util

import pytest

from app.asr.dataset import TARGET_SAMPLE_RATE, SplitSizes, _hours, normalize_arabic

# ── Arabic letters / marks by codepoint (ASCII-only source) ──────────────────────────
ALEF = "ا"  # bare alef
LAM = "ل"
SEEN = "س"
MEEM = "م"
HEH = "ه"
ALEF_HAMZA_ABOVE = "أ"  # MUST NOT fold to bare alef
TEH_MARBUTA = "ة"  # MUST NOT fold to heh
ALEF_MAKSURA = "ى"  # MUST NOT fold to ya
TATWEEL = "ـ"
FATHA = "َ"
DAMMA = "ُ"
SUKUN = "ْ"
SHADDA = "ّ"
ARABIC_COMMA = "،"
ARABIC_QUESTION = "؟"

SALAM = ALEF + LAM + SEEN + LAM + ALEF + MEEM  # "as-salaam", no diacritics


def test_strips_tashkeel():
    voweled = ALEF + LAM + SEEN + FATHA + LAM + SHADDA + ALEF + MEEM + DAMMA
    assert normalize_arabic(voweled) == ALEF + LAM + SEEN + LAM + ALEF + MEEM


def test_removes_tatweel():
    elongated = MEEM + TATWEEL + TATWEEL + HEH + LAM + ALEF
    assert normalize_arabic(elongated) == MEEM + HEH + LAM + ALEF


def test_removes_punctuation_and_collapses_whitespace():
    text = SALAM + ARABIC_COMMA + "   " + SALAM + ARABIC_QUESTION
    assert normalize_arabic(text) == SALAM + " " + SALAM


def test_collapses_ascii_whitespace():
    assert normalize_arabic("a\t\n  b   c") == "a b c"


def test_preserves_hamza_and_ta_marbuta_and_maksura():
    # The deliberate non-folding decision: these variants survive normalization.
    text = ALEF_HAMZA_ABOVE + HEH + LAM + ALEF + " " + MEEM + ALEF_MAKSURA + " " + TEH_MARBUTA
    out = normalize_arabic(text)
    assert ALEF_HAMZA_ABOVE in out
    assert TEH_MARBUTA in out
    assert ALEF_MAKSURA in out


def test_idempotent():
    messy = ALEF + LAM + SEEN + FATHA + LAM + SHADDA + ALEF + MEEM + DAMMA + ARABIC_QUESTION
    once = normalize_arabic(messy)
    assert normalize_arabic(once) == once


def test_empty_and_whitespace_only():
    assert normalize_arabic("") == ""
    assert normalize_arabic("   \t\n ") == ""


def test_hours_computes_from_16khz_arrays():
    # 1s clip + 2s clip = 3 seconds = 3/3600 hours.
    fake_split = [
        {"audio": {"array": [0.0] * TARGET_SAMPLE_RATE}},
        {"audio": {"array": [0.0] * (TARGET_SAMPLE_RATE * 2)}},
    ]
    assert _hours(fake_split) == pytest.approx(3 / 3600)


def test_split_sizes_as_dict_rounds_hours():
    sizes = SplitSizes(n_train=100, n_val=10, n_test=20, train_hours=1.23456)
    assert sizes.as_dict() == {"n_train": 100, "n_val": 10, "n_test": 20, "train_hours": 1.23}


@pytest.mark.skipif(
    importlib.util.find_spec("datasets") is None,
    reason="asr extra not installed (datasets); full download is a host/Colab step",
)
def test_loader_importable_when_deps_present():
    # When the asr extra IS installed, the lazy import path inside the loader resolves.
    # We do NOT download here (Common Voice ar is HF-gated and large).
    from app.asr.dataset import load_common_voice_ar

    assert callable(load_common_voice_ar)
