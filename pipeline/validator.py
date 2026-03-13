"""
Validator: compare pipeline output against the GSheet ground truth.

Matches rows by ``raw_description`` (since txn_ids differ between the
GSheet and our pipeline). Reports per-field accuracy and lists mismatches.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from pipeline.models import CanonicalTransaction


def validate(
    txns: list[CanonicalTransaction],
    benchmark_path: str | Path,
) -> dict:
    """Compare *txns* against the GSheet CSV benchmark.

    Returns a summary dict with:
      - total_rows, matched_rows
      - per-field accuracy (direction, amount, txn_type, channel, upi_type,
        counterparty, counterparty_category)
      - list of mismatches for inspection
    """
    benchmark_path = Path(benchmark_path)
    bench_rows = _load_benchmark(benchmark_path)

    # Build lookup from raw_description → benchmark row
    # (there can be duplicates — same narration on different dates)
    # We use (raw_description, direction, amount) as a composite key.
    bench_lookup: dict[tuple, list[dict]] = defaultdict(list)
    for row in bench_rows:
        key = (row["raw_description"], row["direction"], row["amount"])
        bench_lookup[key].append(row)

    # Track which benchmark rows have been consumed (to handle duplicates)
    consumed: set[int] = set()

    # Fields to compare
    compare_fields = [
        "direction", "amount", "txn_type", "channel", "upi_type",
        "counterparty", "counterparty_category",
    ]
    field_correct: dict[str, int] = {f: 0 for f in compare_fields}
    field_compared: dict[str, int] = {f: 0 for f in compare_fields}

    matched = 0
    mismatches: list[dict] = []

    for txn in txns:
        txn_dict = _txn_to_compare_dict(txn)
        key = (txn_dict["raw_description"], txn_dict["direction"], txn_dict["amount"])
        candidates = bench_lookup.get(key, [])

        # Find an unconsumed matching benchmark row
        bench_row = None
        for i, cand in enumerate(candidates):
            row_idx = id(cand)
            if row_idx not in consumed:
                bench_row = cand
                consumed.add(row_idx)
                break

        if bench_row is None:
            # Try looser match by raw_description only
            for bkey, cands in bench_lookup.items():
                if bkey[0] == txn_dict["raw_description"]:
                    for cand in cands:
                        row_idx = id(cand)
                        if row_idx not in consumed:
                            bench_row = cand
                            consumed.add(row_idx)
                            break
                    if bench_row:
                        break

        if bench_row is None:
            continue

        matched += 1
        row_mismatches = {}

        for field in compare_fields:
            pipeline_val = txn_dict.get(field, "")
            bench_val = bench_row.get(field, "")

            # Only compare if the pipeline actually produced a value
            if not pipeline_val:
                continue

            field_compared[field] += 1

            if _values_match(field, pipeline_val, bench_val):
                field_correct[field] += 1
            else:
                row_mismatches[field] = {
                    "pipeline": pipeline_val,
                    "benchmark": bench_val,
                }

        if row_mismatches:
            mismatches.append({
                "txn_id": txn.txn_id,
                "raw_description": txn.raw_description[:80],
                "mismatches": row_mismatches,
            })

    # Build accuracy report
    accuracy: dict[str, str] = {}
    for field in compare_fields:
        total = field_compared[field]
        correct = field_correct[field]
        if total > 0:
            accuracy[field] = f"{correct}/{total} ({100 * correct / total:.1f}%)"
        else:
            accuracy[field] = "N/A (not filled)"

    return {
        "total_pipeline_rows": len(txns),
        "total_benchmark_rows": len(bench_rows),
        "matched_rows": matched,
        "accuracy": accuracy,
        "mismatches": mismatches,
    }


def print_report(result: dict) -> None:
    """Pretty-print the validation report to stdout."""
    print("\n" + "=" * 70)
    print("VALIDATION REPORT")
    print("=" * 70)
    print(f"Pipeline rows:  {result['total_pipeline_rows']}")
    print(f"Benchmark rows: {result['total_benchmark_rows']}")
    print(f"Matched:        {result['matched_rows']}")
    print()
    print("Per-field accuracy (pipeline vs GSheet ground truth):")
    for field, acc in result["accuracy"].items():
        print(f"  {field:25} {acc}")

    mis = result["mismatches"]
    if mis:
        print(f"\nMismatches ({len(mis)} rows):")
        for m in mis[:20]:
            print(f"  {m['txn_id']}  {m['raw_description'][:60]}")
            for field, vals in m["mismatches"].items():
                print(f"    {field}: pipeline={vals['pipeline']}  benchmark={vals['benchmark']}")
        if len(mis) > 20:
            print(f"  ... and {len(mis) - 20} more")
    else:
        print("\nNo mismatches — perfect agreement on all filled fields!")

    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_benchmark(path: Path) -> list[dict]:
    """Load the GSheet CSV, skipping empty trailing rows."""
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [r for r in rows if r.get("txn_id")]


def _txn_to_compare_dict(txn: CanonicalTransaction) -> dict:
    """Convert a CanonicalTransaction to a flat dict for comparison."""
    return {
        "raw_description": txn.raw_description,
        "direction": txn.direction.value,
        "amount": str(txn.amount),
        "txn_type": txn.txn_type.value if txn.txn_type else "",
        "channel": txn.channel.value if txn.channel else "",
        "upi_type": txn.upi_type.value if txn.upi_type else "",
        "counterparty": txn.counterparty or "",
        "counterparty_category": (
            txn.counterparty_category.value if txn.counterparty_category else ""
        ),
    }


def _values_match(field: str, pipeline_val: str, bench_val: str) -> bool:
    """Compare two field values with field-specific matching logic.

    - amount: exact decimal comparison
    - counterparty: prefix-aware fuzzy matching (handles naming variants like
      "Reliance Jio" vs "Reliance Jio Infocom", "Sterling" vs "Sterling Rent")
    - everything else: case-insensitive exact match
    """
    from decimal import Decimal, InvalidOperation

    if field == "amount":
        try:
            return Decimal(pipeline_val) == Decimal(bench_val)
        except (InvalidOperation, ValueError):
            pass

    p = pipeline_val.lower().strip()
    b = bench_val.lower().strip()

    if p == b:
        return True

    if field == "counterparty":
        # Prefix / substring: one name contains the other
        if p in b or b in p:
            return True
        # Word-set overlap: all words in the shorter name appear in the longer
        p_words = set(p.split())
        b_words = set(b.split())
        if p_words and b_words:
            shorter, longer = sorted([p_words, b_words], key=len)
            if shorter.issubset(longer):
                return True

    return False
