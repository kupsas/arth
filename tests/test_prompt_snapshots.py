"""
Golden snapshot tests: verify the YAML-based prompt functions produce
byte-identical output to the pre-migration baselines.

These are the single most important tests for the prompt migration.
If these pass, we know the YAML files faithfully reproduce the original
hardcoded Python-string prompts.

The golden snapshot JSON files were captured by running
``tests/capture_golden_snapshots.py`` against the ORIGINAL prompts.py
before the YAML rewrite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.prompts import (
    batch_classify_prompt,
    two_pass_category_prompt,
    two_pass_fields_prompt,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_golden(filename: str) -> dict:
    path = FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _diff_context(actual: str, expected: str, label: str) -> str:
    """Build a human-readable diff message showing the first mismatch."""
    for i, (a, b) in enumerate(zip(actual, expected)):
        if a != b:
            start = max(0, i - 30)
            return (
                f"{label} mismatch at char {i}:\n"
                f"  got:      ...{actual[start:i+30]!r}...\n"
                f"  expected: ...{expected[start:i+30]!r}..."
            )
    if len(actual) != len(expected):
        return f"{label} length mismatch: got {len(actual)}, expected {len(expected)}"
    return ""


# ── Single-pass snapshot tests ──────────────────────────────────────────────

class TestSinglePassSnapshot:
    """Verify batch_classify_prompt matches its golden snapshot."""

    @pytest.fixture
    def golden(self) -> dict:
        return _load_golden("golden_single_pass.json")

    def test_system_matches_snapshot(
        self, sample_prompt_items: list[dict], golden: dict
    ) -> None:
        system, _ = batch_classify_prompt(sample_prompt_items)
        assert system == golden["system"], _diff_context(
            system, golden["system"], "system"
        )

    def test_user_matches_snapshot(
        self, sample_prompt_items: list[dict], golden: dict
    ) -> None:
        _, user = batch_classify_prompt(sample_prompt_items)
        assert user == golden["user"], _diff_context(
            user, golden["user"], "user"
        )


# ── Two-pass fields snapshot tests ──────────────────────────────────────────

class TestTwoPassFieldsSnapshot:
    """Verify two_pass_fields_prompt matches its golden snapshot."""

    @pytest.fixture
    def golden(self) -> dict:
        return _load_golden("golden_two_pass_fields.json")

    def test_system_matches_snapshot(
        self, sample_prompt_items: list[dict], golden: dict
    ) -> None:
        system, _ = two_pass_fields_prompt(sample_prompt_items)
        assert system == golden["system"], _diff_context(
            system, golden["system"], "system"
        )

    def test_user_matches_snapshot(
        self, sample_prompt_items: list[dict], golden: dict
    ) -> None:
        _, user = two_pass_fields_prompt(sample_prompt_items)
        assert user == golden["user"], _diff_context(
            user, golden["user"], "user"
        )


# ── Two-pass category snapshot tests ────────────────────────────────────────

class TestTwoPassCategorySnapshot:
    """Verify two_pass_category_prompt matches its golden snapshot."""

    @pytest.fixture
    def golden(self) -> dict:
        return _load_golden("golden_two_pass_category.json")

    def test_system_matches_snapshot(
        self, sample_two_pass_category_items: list[dict], golden: dict
    ) -> None:
        system, _ = two_pass_category_prompt(sample_two_pass_category_items)
        assert system == golden["system"], _diff_context(
            system, golden["system"], "system"
        )

    def test_user_matches_snapshot(
        self, sample_two_pass_category_items: list[dict], golden: dict
    ) -> None:
        _, user = two_pass_category_prompt(sample_two_pass_category_items)
        assert user == golden["user"], _diff_context(
            user, golden["user"], "user"
        )
