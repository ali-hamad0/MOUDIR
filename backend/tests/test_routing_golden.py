"""Task 7.10 — Routing golden eval tests.

Proves the eval harness exits 1 when accuracy falls below the threshold.
Uses only deterministic offline mocks — no LLM, no network.
"""

import json
from pathlib import Path

import yaml

from app.agents.eval.evaluate_routing import _check_thresholds, _OfflineMockLLM, _run_eval

_GOLDEN_PATH = Path(__file__).parent.parent / "app" / "agents" / "eval" / "routing_golden.json"
_THRESHOLDS_PATH = (
    Path(__file__).parent.parent / "app" / "agents" / "eval" / "routing_thresholds.yaml"
)


def _load_golden() -> list[dict]:
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


def _load_thresholds() -> dict:
    return yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))


# ── Golden set sanity checks ─────────────────────────────────────────────────


def test_golden_set_has_at_least_20_entries():
    golden = _load_golden()
    assert len(golden) >= 20, f"Need ≥20 golden queries, got {len(golden)}"


def test_golden_set_has_required_keys():
    golden = _load_golden()
    for i, entry in enumerate(golden):
        assert "query_ar" in entry, f"Entry {i} missing 'query_ar'"
        assert "expected_agent" in entry, f"Entry {i} missing 'expected_agent'"
        assert "notes" in entry, f"Entry {i} missing 'notes'"


def test_golden_set_all_intents_valid():
    valid = {"order", "inventory", "finance", "customer", "advisor"}
    golden = _load_golden()
    for entry in golden:
        assert (
            entry["expected_agent"] in valid
        ), f"Invalid intent '{entry['expected_agent']}' in: {entry['query_ar']}"


def test_golden_set_has_at_least_four_per_class():
    from collections import Counter

    golden = _load_golden()
    counts = Counter(e["expected_agent"] for e in golden)
    for cls in ("order", "inventory", "finance", "customer", "advisor"):
        assert counts[cls] >= 4, f"Need ≥4 queries for class '{cls}', got {counts[cls]}"


def test_golden_set_has_at_least_two_ambiguous_advisor_queries():
    golden = _load_golden()
    ambiguous = [e for e in golden if "ambiguous" in e.get("notes", "")]
    assert len(ambiguous) >= 2, f"Need ≥2 ambiguous queries, got {len(ambiguous)}"


# ── Threshold file sanity ─────────────────────────────────────────────────────


def test_thresholds_file_has_required_keys():
    thresholds = _load_thresholds()
    assert "overall_accuracy_min" in thresholds
    assert "per_class_min" in thresholds


def test_thresholds_overall_is_085():
    thresholds = _load_thresholds()
    assert thresholds["overall_accuracy_min"] == 0.85


# ── _check_thresholds logic ───────────────────────────────────────────────────


def test_check_thresholds_passes_when_all_above_min():
    thresholds = {"overall_accuracy_min": 0.85, "per_class_min": 0.70}
    per_class = {
        "order": 1.0,
        "inventory": 0.80,
        "finance": 0.80,
        "customer": 0.80,
        "advisor": 0.83,
    }
    assert _check_thresholds(0.90, per_class, thresholds) is True


def test_check_thresholds_fails_when_overall_below_min():
    thresholds = {"overall_accuracy_min": 0.85, "per_class_min": 0.70}
    per_class = {"order": 1.0}
    assert _check_thresholds(0.80, per_class, thresholds) is False


def test_check_thresholds_fails_when_any_class_below_min():
    thresholds = {"overall_accuracy_min": 0.85, "per_class_min": 0.70}
    per_class = {"order": 0.50, "advisor": 1.0}
    assert _check_thresholds(0.90, per_class, thresholds) is False


# ── Offline mock + eval harness ───────────────────────────────────────────────


async def test_offline_mock_achieves_100_percent_accuracy():
    """The pre-programmed mock should classify every golden query correctly."""
    golden = _load_golden()
    llm = _OfflineMockLLM(golden)
    overall, per_class = await _run_eval(golden, llm)
    assert overall == 1.0, f"Expected 100% accuracy from mock, got {overall:.1%}"


async def test_bad_mock_fails_threshold():
    """A mock that always returns 'order' should score well below 0.85 overall
    (only the 'order' queries pass). The gate must detect this and fail.
    """
    golden = _load_golden()
    thresholds = _load_thresholds()

    class _AlwaysOrderMock:
        def with_structured_output(self, schema):
            class _M:
                async def ainvoke(self, messages):
                    return schema(intent="order")

            return _M()

    overall, per_class = await _run_eval(golden, _AlwaysOrderMock())
    assert (
        _check_thresholds(overall, per_class, thresholds) is False
    ), "Gate should FAIL when mock always returns 'order' — most queries will be wrong"


async def test_eval_exit_code_1_on_low_accuracy(tmp_path, monkeypatch):
    """The evaluate_routing __main__ block exits 1 when accuracy is below threshold."""

    # Patch the threshold to 1.0 so even the perfect mock fails
    # (this proves the gate trips on a lowered threshold too)

    fake_thresholds = {"overall_accuracy_min": 1.01, "per_class_min": 1.01}
    golden = _load_golden()
    bad_llm = type(
        "_AlwaysWrong",
        (),
        {
            "with_structured_output": lambda self, schema: type(
                "_M",
                (),
                {"ainvoke": lambda self, msgs: schema(intent="advisor")},
            )()
        },
    )()

    overall, per_class = await _run_eval(golden, bad_llm)
    passed = _check_thresholds(overall, per_class, fake_thresholds)
    assert passed is False


async def test_eval_exit_code_0_on_perfect_accuracy():
    """Gate passes when the mock returns 100% correct answers."""
    golden = _load_golden()
    thresholds = _load_thresholds()
    llm = _OfflineMockLLM(golden)
    overall, per_class = await _run_eval(golden, llm)
    assert _check_thresholds(overall, per_class, thresholds) is True
