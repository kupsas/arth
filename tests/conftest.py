"""
Shared pytest fixtures for the Arth test suite.

Provides sample transaction items in the format that prompt functions expect,
loaded from the benchmark fixture file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _disable_imf_network_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup + scheduler inflation sync use IMF SDMX; tests should not depend on it."""
    monkeypatch.setenv("INFLATION_DISABLE_IMF", "1")


REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_FIXTURE = REPO_ROOT / "data" / "test" / "benchmark_20.json"
GOLDEN_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def benchmark_fixture_raw() -> list[dict]:
    """Load the raw benchmark_20.json fixture data."""
    with open(BENCHMARK_FIXTURE) as f:
        return json.load(f)


@pytest.fixture
def sample_prompt_items(benchmark_fixture_raw: list[dict]) -> list[dict]:
    """Build prompt-ready item dicts from the first 5 benchmark transactions.

    These are in the exact format that batch_classify_prompt() and friends
    expect: id, txn_date, desc, direction, amount, channel, txn_type,
    upi_type, ref_number, needs.

    We use 5 items to keep snapshot files small while covering diverse cases:
      [0] UPI P2M (Spotify)
      [1] UPI P2M (Amazon)
      [2] UPI P2M (Swiggy)
      [3] BANK salary (NEFT CR)
      [4] UPI P2M (Apollo Pharmacy)
    """
    items = []
    for i, row in enumerate(benchmark_fixture_raw[:5]):
        needs = []
        if row.get("expected_txn_type"):
            needs.append("txn_type")
        if row.get("expected_upi_type") and row["expected_upi_type"] != "NA":
            needs.append("upi_type")
        needs.append("counterparty")
        needs.append("counterparty_category")

        items.append({
            "id": f"T_99{i:06d}",
            "txn_date": row["txn_date"],
            "desc": row["raw_description"],
            "direction": row["direction"],
            "amount": row["amount"],
            "channel": row.get("channel", ""),
            "txn_type": "",
            "upi_type": "",
            "ref_number": row.get("ref_number", ""),
            "needs": ", ".join(f'"{n}"' for n in needs),
        })
    return items


@pytest.fixture
def sample_two_pass_category_items(benchmark_fixture_raw: list[dict]) -> list[dict]:
    """Build items for two_pass_category_prompt() — needs txn_type_counterparty field.

    Simulates what pass 2 receives: already-resolved txn_type + counterparty
    combined into a single string.
    """
    combos = [
        ("UPI_EXPENSE", "Spotify"),
        ("UPI_EXPENSE", "Amazon"),
        ("UPI_EXPENSE", "Swiggy"),
        # Fictional name aligned with prompts/classify_two_pass_category.yaml examples.
        ("INCOME_SALARY", "Rohan K Mehta"),
        ("UPI_EXPENSE", "Apollo Pharmacy"),
    ]
    items = []
    for i, (row, (txn_type, cp)) in enumerate(
        zip(benchmark_fixture_raw[:5], combos)
    ):
        items.append({
            "id": f"T_99{i:06d}",
            "txn_type_counterparty": f"{txn_type} {cp}",
            "direction": row["direction"],
            "amount": row["amount"],
            "channel": row.get("channel", ""),
        })
    return items
