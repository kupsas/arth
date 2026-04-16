#!/usr/bin/env python3
"""
One-shot script: capture golden snapshots from the CURRENT prompt functions.

Run this ONCE before rewriting prompts.py to YAML. It saves the exact
(system, user) output for each prompt function so we can verify the
YAML-based version produces identical results.

Usage:
    python3 -m tests.capture_golden_snapshots
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BENCHMARK_FILE = Path(__file__).resolve().parent.parent / "data" / "test" / "benchmark_20.json"


def _load_benchmark() -> list[dict]:
    with open(BENCHMARK_FILE) as f:
        return json.load(f)


def _build_prompt_items(rows: list[dict]) -> list[dict]:
    """Build the 5 items used in conftest.py's sample_prompt_items fixture."""
    items = []
    for i, row in enumerate(rows[:5]):
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


def _build_two_pass_cat_items(rows: list[dict]) -> list[dict]:
    """Build items for two_pass_category_prompt (pass-2 format)."""
    combos = [
        ("UPI_EXPENSE", "Spotify"),
        ("UPI_EXPENSE", "Amazon"),
        ("UPI_EXPENSE", "Swiggy"),
        ("INCOME_SALARY", "Rohan K Mehta"),
        ("UPI_EXPENSE", "Apollo Pharmacy"),
    ]
    items = []
    for i, (row, (txn_type, cp)) in enumerate(zip(rows[:5], combos)):
        items.append({
            "id": f"T_99{i:06d}",
            "txn_type_counterparty": f"{txn_type} {cp}",
            "direction": row["direction"],
            "amount": row["amount"],
            "channel": row.get("channel", ""),
        })
    return items


def main() -> None:
    from pipeline.prompts import (
        batch_classify_prompt,
        two_pass_category_prompt,
        two_pass_fields_prompt,
    )

    rows = _load_benchmark()
    prompt_items = _build_prompt_items(rows)
    cat_items = _build_two_pass_cat_items(rows)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    # --- Single-pass ---
    sys_msg, usr_msg = batch_classify_prompt(prompt_items)
    snapshot = {"system": sys_msg, "user": usr_msg}
    out = FIXTURES_DIR / "golden_single_pass.json"
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"Wrote {out}  ({len(sys_msg)} + {len(usr_msg)} chars)")

    # --- Two-pass fields ---
    sys_msg, usr_msg = two_pass_fields_prompt(prompt_items)
    snapshot = {"system": sys_msg, "user": usr_msg}
    out = FIXTURES_DIR / "golden_two_pass_fields.json"
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"Wrote {out}  ({len(sys_msg)} + {len(usr_msg)} chars)")

    # --- Two-pass category ---
    sys_msg, usr_msg = two_pass_category_prompt(cat_items)
    snapshot = {"system": sys_msg, "user": usr_msg}
    out = FIXTURES_DIR / "golden_two_pass_category.json"
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"Wrote {out}  ({len(sys_msg)} + {len(usr_msg)} chars)")

    print("\nDone. Golden snapshots captured.")


if __name__ == "__main__":
    main()
