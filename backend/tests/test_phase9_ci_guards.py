"""Phase 9 CI guard tests (Task 9.5).

Structural smoke-tests that verify every Phase 9 deliverable exists and meets
its minimal contract.  No DB, no network — all assertions are file-system or
import checks.  These run in the normal pytest suite and block the branch from
merging if any deliverable is missing or broken.
"""

from __future__ import annotations

from pathlib import Path

# Repo root is two levels above this file: backend/tests/ → backend/ → repo root
_REPO = Path(__file__).parent.parent.parent


# ── 1. Open-source scaffolding ────────────────────────────────────────────────


def test_license_exists():
    """LICENSE must be present at repo root."""
    assert (_REPO / "LICENSE").exists(), "LICENSE file missing"


def test_license_contains_mit():
    """LICENSE must be MIT (not a blank placeholder)."""
    content = (_REPO / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in content, "LICENSE does not contain MIT License header"
    assert "Ali Hamad" in content, "LICENSE does not contain copyright holder name"


def test_contributing_exists():
    """CONTRIBUTING.md must be present at repo root."""
    assert (_REPO / "CONTRIBUTING.md").exists(), "CONTRIBUTING.md missing"


def test_contributing_references_constitution():
    """CONTRIBUTING.md must reference constitution.md so contributors know the rules."""
    content = (_REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "constitution.md" in content, "CONTRIBUTING.md does not reference constitution.md"


def test_security_exists():
    """SECURITY.md must be present at repo root."""
    assert (_REPO / "SECURITY.md").exists(), "SECURITY.md missing"


def test_security_has_contact():
    """SECURITY.md must have a reporting contact (email or GitHub advisory)."""
    content = (_REPO / "SECURITY.md").read_text(encoding="utf-8")
    has_email = "@" in content
    has_advisory = "advisory" in content.lower() or "github.com" in content
    assert has_email or has_advisory, "SECURITY.md has no identifiable reporting contact"


# ── 3. Demo seed script ───────────────────────────────────────────────────────


def test_seed_demo_exists():
    """backend/scripts/seed_demo.py must be present."""
    assert (
        _REPO / "backend" / "scripts" / "seed_demo.py"
    ).exists(), "backend/scripts/seed_demo.py missing"


def test_seed_demo_is_not_a_test_fixture():
    """seed_demo.py must not import pytest — it is a dev helper, not a test."""
    content = (_REPO / "backend" / "scripts" / "seed_demo.py").read_text(encoding="utf-8")
    assert "import pytest" not in content, "seed_demo.py must not import pytest"


def test_seed_demo_is_idempotent_by_design():
    """seed_demo.py must contain an idempotency check (skip-if-exists pattern)."""
    content = (_REPO / "backend" / "scripts" / "seed_demo.py").read_text(encoding="utf-8")
    # The script uses _get_or_skip / _any_rows helpers for idempotency
    assert (
        "_get_or_skip" in content or "scalar_one_or_none" in content
    ), "seed_demo.py does not appear to check for existing rows before inserting"


def test_seed_demo_seeds_Lebanese_products():
    """seed_demo.py must contain Arabic product names."""
    content = (_REPO / "backend" / "scripts" / "seed_demo.py").read_text(encoding="utf-8")
    assert "كعك" in content, "seed_demo.py does not contain Arabic product name for ka'ak"
    assert "بقلاوة" in content, "seed_demo.py does not contain Arabic product name for baklawa"


# ── 5. README polish ──────────────────────────────────────────────────────────


def test_readme_has_ci_badge():
    """README.md must have a CI badge (GitHub Actions)."""
    content = (_REPO / "README.md").read_text(encoding="utf-8")
    assert "actions/workflows/ci.yml/badge.svg" in content, "README.md missing CI badge"


def test_readme_has_demo_section():
    """README.md must have a Demo section."""
    content = (_REPO / "README.md").read_text(encoding="utf-8")
    assert "## Demo" in content, "README.md missing ## Demo section"


def test_readme_has_for_reviewers_section():
    """README.md must have a For Reviewers section."""
    content = (_REPO / "README.md").read_text(encoding="utf-8")
    assert "## For Reviewers" in content, "README.md missing ## For Reviewers section"
