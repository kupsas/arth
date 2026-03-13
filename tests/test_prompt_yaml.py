"""
YAML structural validation: verify the prompt YAML files are well-formed,
have required metadata, and stay in sync with pipeline/models.py enums.

These tests catch two classes of bugs:
  1. Malformed YAML (syntax errors, missing keys)
  2. Enum drift (someone adds a category to models.py but forgets the YAML)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.models import CounterpartyCategory, TxnType, UPIType

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

PROMPT_YAMLS = [
    "classify_single_pass.yaml",
    "classify_two_pass_fields.yaml",
    "classify_two_pass_category.yaml",
]


# ── All YAML files parse without error ──────────────────────────────────────

@pytest.mark.parametrize("filename", list(PROMPTS_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_all_yaml_files_parse(filename: Path) -> None:
    data = yaml.safe_load(filename.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{filename.name} did not parse to a dict"


# ── Prompt YAMLs have required metadata keys ────────────────────────────────

@pytest.mark.parametrize("filename", PROMPT_YAMLS)
def test_required_metadata_keys(filename: str) -> None:
    data = yaml.safe_load((PROMPTS_DIR / filename).read_text(encoding="utf-8"))
    for key in ("version", "description", "system_template"):
        assert key in data, f"{filename} missing required key: {key}"
    assert isinstance(data["system_template"], str)
    assert len(data["system_template"]) > 100, (
        f"{filename} system_template looks suspiciously short"
    )


# ── Enum values in enums.yaml match pipeline/models.py ──────────────────────

class TestEnumSync:
    """Verify that the YAML enum strings contain all values from models.py."""

    @pytest.fixture
    def enums(self) -> dict:
        return yaml.safe_load(
            (PROMPTS_DIR / "enums.yaml").read_text(encoding="utf-8")
        )

    def test_txn_types_match(self, enums: dict) -> None:
        yaml_types = {t.strip() for t in enums["txn_types"].replace("\n", ",").split(",")}
        yaml_types.discard("")
        model_types = {t.value for t in TxnType}
        assert yaml_types == model_types, (
            f"txn_types mismatch:\n"
            f"  in YAML but not models.py: {yaml_types - model_types}\n"
            f"  in models.py but not YAML: {model_types - yaml_types}"
        )

    def test_upi_types_match(self, enums: dict) -> None:
        yaml_types = {t.strip() for t in enums["upi_types"].split(",")}
        yaml_types.discard("")
        # Only P2P and P2M are used in prompts (LITE_SELF_FUND and NA are
        # internal values, not offered to the LLM as classification choices)
        expected = {"P2P", "P2M"}
        assert yaml_types == expected, (
            f"upi_types mismatch: got {yaml_types}, expected {expected}"
        )

    def test_categories_match(self, enums: dict) -> None:
        yaml_cats = {c.strip() for c in enums["categories"].split("\n")}
        yaml_cats.discard("")
        model_cats = {c.value for c in CounterpartyCategory}
        assert yaml_cats == model_cats, (
            f"categories mismatch:\n"
            f"  in YAML but not models.py: {yaml_cats - model_cats}\n"
            f"  in models.py but not YAML: {model_cats - yaml_cats}"
        )


# ── Few-shot examples are well-formed ───────────────────────────────────────

class TestFewShotExamples:
    """Validate the structured few-shot examples in few_shot_examples.yaml."""

    @pytest.fixture
    def examples(self) -> list[dict]:
        data = yaml.safe_load(
            (PROMPTS_DIR / "few_shot_examples.yaml").read_text(encoding="utf-8")
        )
        return data["examples"]

    def test_has_examples(self, examples: list[dict]) -> None:
        assert len(examples) >= 10, "Expected at least 10 few-shot examples"

    def test_examples_have_required_fields(self, examples: list[dict]) -> None:
        required = {"number", "title", "desc", "direction", "amount", "channel",
                     "txn_type", "counterparty", "counterparty_category"}
        for ex in examples:
            missing = required - set(ex.keys())
            assert not missing, (
                f"Example {ex.get('number', '?')} missing fields: {missing}"
            )

    def test_txn_type_values_valid(self, examples: list[dict]) -> None:
        valid = {t.value for t in TxnType}
        for ex in examples:
            assert ex["txn_type"] in valid, (
                f"Example {ex['number']}: invalid txn_type {ex['txn_type']!r}"
            )

    def test_category_values_valid(self, examples: list[dict]) -> None:
        valid = {c.value for c in CounterpartyCategory}
        for ex in examples:
            assert ex["counterparty_category"] in valid, (
                f"Example {ex['number']}: invalid category "
                f"{ex['counterparty_category']!r}"
            )

    def test_upi_type_values_valid(self, examples: list[dict]) -> None:
        valid = {t.value for t in UPIType}
        for ex in examples:
            if "upi_type" in ex:
                assert ex["upi_type"] in valid, (
                    f"Example {ex['number']}: invalid upi_type {ex['upi_type']!r}"
                )

    def test_numbers_are_sequential(self, examples: list[dict]) -> None:
        numbers = [ex["number"] for ex in examples]
        assert numbers == list(range(1, len(examples) + 1)), (
            f"Example numbers are not sequential: {numbers}"
        )
