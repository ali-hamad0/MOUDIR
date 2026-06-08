"""Routing golden eval — Task 7.10.

Run with the offline mock (CI default):
    python -m app.agents.eval.evaluate_routing

Run with the real Gemini LLM (local only — costs money):
    ROUTING_EVAL_REAL_LLM=1 python -m app.agents.eval.evaluate_routing

Exits 0 if overall accuracy >= overall_accuracy_min and all per-class
accuracies >= per_class_min (from routing_thresholds.yaml). Exits 1 otherwise.

The offline mock is pre-programmed: it looks up each golden query string in
the system-message content and returns the expected intent. This tests the
eval harness and CI gate, not the LLM. Set ROUTING_EVAL_REAL_LLM=1 to test
actual LLM routing accuracy.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

# Ensure Arabic text prints correctly on Windows terminals that default to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).parent
_GOLDEN_PATH = _HERE / "routing_golden.json"
_THRESHOLDS_PATH = _HERE / "routing_thresholds.yaml"


# ── Offline mock LLM ─────────────────────────────────────────────────────────


class _MockStructuredModel:
    """Returned by _OfflineMockLLM.with_structured_output(). Looks up the
    query string in the system-message content and returns the mapped intent.
    """

    def __init__(self, query_to_intent: dict[str, str], schema):
        self._map = query_to_intent
        self._schema = schema

    async def ainvoke(self, messages):
        content = messages[0].content if messages else ""
        for query, intent in self._map.items():
            if query in content:
                return self._schema(intent=intent)
        return self._schema(intent="advisor")


class _OfflineMockLLM:
    """Pre-programmed stub that returns the expected intent for golden queries.

    Built from the golden set so CI runs offline, deterministically, without
    any network call. The accuracy is always 100% for known queries — the goal
    is to verify the eval harness and CI gate, not the LLM intelligence.
    Use ROUTING_EVAL_REAL_LLM=1 for the genuine accuracy measurement.
    """

    def __init__(self, golden: list[dict]):
        self._map = {e["query_ar"]: e["expected_agent"] for e in golden}

    def with_structured_output(self, schema):
        return _MockStructuredModel(self._map, schema)


# ── Eval logic ───────────────────────────────────────────────────────────────


async def _run_eval(golden: list[dict], llm) -> tuple[float, dict[str, float]]:
    """Classify all golden queries and return (overall_acc, per_class_acc)."""
    from app.agents.supervisor.routing import classify_intent

    correct = 0
    per_class: dict[str, list[bool]] = {}

    for entry in golden:
        query = entry["query_ar"]
        expected = entry["expected_agent"]
        got = await classify_intent(query, llm)
        ok = got == expected
        if ok:
            correct += 1
        per_class.setdefault(expected, []).append(ok)
        status = "OK" if ok else f"FAIL (got {got!r})"
        print(f"  [{expected:9s}] {status}  {query[:50]}")

    overall = correct / len(golden) if golden else 0.0
    per_class_acc = {cls: sum(results) / len(results) for cls, results in per_class.items()}
    return overall, per_class_acc


def _check_thresholds(
    overall: float,
    per_class: dict[str, float],
    thresholds: dict,
) -> bool:
    """Return True if all thresholds pass, False otherwise. Prints results."""
    overall_min: float = thresholds.get("overall_accuracy_min", 0.85)
    per_class_min: float = thresholds.get("per_class_min", 0.70)

    print(f"\nOverall accuracy : {overall:.1%}  (min {overall_min:.0%})")
    passed = overall >= overall_min

    print("Per-class accuracy:")
    for cls, acc in sorted(per_class.items()):
        ok = acc >= per_class_min
        mark = "OK" if ok else "FAIL"
        print(f"  {mark:4s} {cls:9s}: {acc:.1%}  (min {per_class_min:.0%})")
        if not ok:
            passed = False

    return passed


async def _main() -> int:
    golden: list[dict] = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    thresholds: dict = yaml.safe_load(_THRESHOLDS_PATH.read_text(encoding="utf-8"))

    use_real_llm = os.environ.get("ROUTING_EVAL_REAL_LLM", "").strip() == "1"
    if use_real_llm:
        from app.agents.llm.router import GeminiRouter
        from app.infra.settings import get_settings

        settings = get_settings()
        llm = GeminiRouter(settings).tier1()
        print("Running with REAL Gemini LLM (charges apply)\n")
    else:
        llm = _OfflineMockLLM(golden)
        print("Running with offline mock LLM (CI mode)\n")

    print(f"Golden set: {len(golden)} queries\n")
    overall, per_class = await _run_eval(golden, llm)
    passed = _check_thresholds(overall, per_class, thresholds)

    if passed:
        print("\nAll routing thresholds passed.")
        return 0
    else:
        print("\nRouting accuracy below threshold — see failures above.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
