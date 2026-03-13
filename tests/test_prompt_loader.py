"""
Unit tests for the prompt loader logic in pipeline/prompts.py.

Tests that the YAML-backed prompt functions return correct types,
include expected content, and handle edge cases.
"""

from __future__ import annotations

import pytest

from pipeline.prompts import (
    batch_classify_prompt,
    two_pass_category_prompt,
    two_pass_fields_prompt,
)


# ── Return type checks ─────────────────────────────────────────────────────

class TestReturnTypes:
    def test_batch_classify_returns_tuple(
        self, sample_prompt_items: list[dict]
    ) -> None:
        result = batch_classify_prompt(sample_prompt_items)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_two_pass_fields_returns_tuple(
        self, sample_prompt_items: list[dict]
    ) -> None:
        result = two_pass_fields_prompt(sample_prompt_items)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_two_pass_category_returns_tuple(
        self, sample_two_pass_category_items: list[dict]
    ) -> None:
        result = two_pass_category_prompt(sample_two_pass_category_items)
        assert isinstance(result, tuple)
        assert len(result) == 2


# ── System message content checks ──────────────────────────────────────────

class TestSystemContent:
    def test_single_pass_contains_txn_types(
        self, sample_prompt_items: list[dict]
    ) -> None:
        system, _ = batch_classify_prompt(sample_prompt_items)
        assert "UPI_EXPENSE" in system
        assert "INCOME_SALARY" in system
        assert "LOAN_INSURANCE_PAYMENT" in system

    def test_single_pass_contains_categories(
        self, sample_prompt_items: list[dict]
    ) -> None:
        system, _ = batch_classify_prompt(sample_prompt_items)
        assert "Entertainment & Events" in system
        assert "Swiggy" in system
        assert "Utilities & Internet" in system

    def test_single_pass_contains_few_shot(
        self, sample_prompt_items: list[dict]
    ) -> None:
        system, _ = batch_classify_prompt(sample_prompt_items)
        # Verify a few recognizable counterparties from the examples
        assert "Spotify" in system
        assert "Swiggy" in system
        assert "Apollo Pharmacy" in system
        assert "IDFC FIRST Bank" in system

    def test_single_pass_no_unresolved_placeholders(
        self, sample_prompt_items: list[dict]
    ) -> None:
        system, _ = batch_classify_prompt(sample_prompt_items)
        assert "{txn_types}" not in system
        assert "{upi_types}" not in system
        assert "{categories}" not in system
        assert "{few_shot}" not in system

    def test_two_pass_fields_contains_uber_hint(
        self, sample_prompt_items: list[dict]
    ) -> None:
        """Two-pass-fields has a unique Uber/Ola P2P hint not in single-pass."""
        system, _ = two_pass_fields_prompt(sample_prompt_items)
        assert "Uber/Ola rides" in system

    def test_two_pass_category_no_few_shot(
        self, sample_two_pass_category_items: list[dict]
    ) -> None:
        """Two-pass-category uses inline examples, not the shared few-shot block."""
        system, _ = two_pass_category_prompt(sample_two_pass_category_items)
        # The shared few-shot block has "Example 1 —" format
        assert "Example 1" not in system
        # But it does have inline examples like "UPI_EXPENSE Spotify"
        assert '"UPI_EXPENSE Spotify"' in system


# ── User message content checks ─────────────────────────────────────────────

class TestUserContent:
    def test_single_pass_contains_all_item_ids(
        self, sample_prompt_items: list[dict]
    ) -> None:
        _, user = batch_classify_prompt(sample_prompt_items)
        for item in sample_prompt_items:
            assert item["id"] in user, f"Item {item['id']} not found in user message"

    def test_two_pass_fields_contains_all_item_ids(
        self, sample_prompt_items: list[dict]
    ) -> None:
        _, user = two_pass_fields_prompt(sample_prompt_items)
        for item in sample_prompt_items:
            assert item["id"] in user

    def test_two_pass_category_contains_all_item_ids(
        self, sample_two_pass_category_items: list[dict]
    ) -> None:
        _, user = two_pass_category_prompt(sample_two_pass_category_items)
        for item in sample_two_pass_category_items:
            assert item["id"] in user

    def test_single_pass_user_prefix(
        self, sample_prompt_items: list[dict]
    ) -> None:
        _, user = batch_classify_prompt(sample_prompt_items)
        assert user.startswith("Classify these transactions:")

    def test_two_pass_fields_user_prefix(
        self, sample_prompt_items: list[dict]
    ) -> None:
        _, user = two_pass_fields_prompt(sample_prompt_items)
        assert user.startswith("Classify these transactions (pass 1")

    def test_two_pass_category_user_prefix(
        self, sample_two_pass_category_items: list[dict]
    ) -> None:
        _, user = two_pass_category_prompt(sample_two_pass_category_items)
        assert user.startswith("Categorise these transactions (pass 2")


# ── Edge cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_items_list(self) -> None:
        """Functions should handle an empty items list without crashing."""
        system, user = batch_classify_prompt([])
        assert len(system) > 100
        assert "Classify these transactions:" in user

    def test_single_item(self) -> None:
        """A batch of 1 should work fine."""
        items = [{
            "id": "T_00000001",
            "desc": "UPI-TEST-test@upi",
            "txn_date": "2025-01-01",
            "direction": "OUTFLOW",
            "amount": "100",
            "channel": "UPI",
            "txn_type": "",
            "upi_type": "",
            "ref_number": "",
            "needs": '"counterparty", "counterparty_category"',
        }]
        system, user = batch_classify_prompt(items)
        assert "T_00000001" in user
        assert user.count("T_00000001") == 1

    def test_item_with_known_txn_type(self) -> None:
        """When txn_type is pre-filled, it appears as txn_type_known in the prompt."""
        items = [{
            "id": "T_00000001",
            "desc": "UPI-TEST-test@upi",
            "txn_date": "2025-01-01",
            "direction": "OUTFLOW",
            "amount": "100",
            "channel": "UPI",
            "txn_type": "UPI_EXPENSE",
            "upi_type": "P2M",
            "ref_number": "",
            "needs": '"counterparty", "counterparty_category"',
        }]
        _, user = batch_classify_prompt(items)
        assert '"txn_type_known":"UPI_EXPENSE"' in user
        assert '"upi_type_known":"P2M"' in user
