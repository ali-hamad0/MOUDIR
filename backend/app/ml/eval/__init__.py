"""Golden evaluation (Phase 6, Task 6.11).

Frozen, committed golden sets (golden/*.json) + per-model floors (thresholds.yaml). The
evaluator loads the REAL artifacts and asserts each clears its threshold; CI runs it so that
intentionally breaking a model turns the build red. `build_golden.py` regenerates the sets.
"""
