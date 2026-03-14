#!/usr/bin/env python3
"""
Generate skeleton benchmark CSVs for manual ground-truth labelling.

Runs Parse + Transform only (no rules classifier, no LLM) and writes
CSVs with pre-filled identity fields and blank classification columns
for the user to fill in via Google Sheets.

Output files:
    data/test/benchmark_hdfc_cc.csv        — May–Dec 2025 (1905 card)
    data/test/benchmark_hdfc_cc_5778.csv   — all transactions on 5778 card
    data/test/benchmark_icici_savings.csv  — all ICICI savings transactions

Usage:
    python3 scripts/export_benchmark.py

After running:
    1. Import the CSV(s) into Google Sheets.
    2. Fill in the blank columns: txn_type, channel, upi_type,
       counterparty, counterparty_category, notes.
    3. Export back as CSVs and drop in docs/personal-data/.
    4. Run validation:
         python3 -m pipeline.run --source hdfc_cc_1905 --validate \\
             --benchmark docs/personal-data/benchmark_hdfc_cc.csv
         python3 -m pipeline.run --source hdfc_cc_5778 --validate \\
             --benchmark docs/personal-data/benchmark_hdfc_cc_5778.csv
         python3 -m pipeline.run --source icici_savings --validate \\
             --benchmark docs/personal-data/benchmark_icici_savings.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Allow running as "python3 scripts/export_benchmark.py" from repo root
# without needing PYTHONPATH or pip install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.parsers.hdfc_cc import HDFCCreditCardParser
from pipeline.parsers.icici_savings import ICICISavingsParser
from pipeline.transformer import transform

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
OUTPUT_DIR = config.REPO_ROOT / "data" / "test"

# ---------------------------------------------------------------------------
# CC 1905 scope: May–Dec 2025 (8 statement months)
#   Aligns with Swiggy app order history (only visible from May 2025).
#   May–Sep 2025 → old format (single-tilde delimiter)
#   Oct–Dec 2025 → new format (tilde-pipe-tilde delimiter)
#
# We filter by statement date prefixes in the filename. Billing cycles run
# ~15th to ~15th, so May statement may include some Apr 2025 txns.
# ---------------------------------------------------------------------------
_CC_BENCHMARK_MONTHS = frozenset({
    "may", "jun", "jul", "aug", "sep",
    "oct", "nov", "dec",
})

# ---------------------------------------------------------------------------
# Skeleton column order (matches the GSheet benchmark format)
# ---------------------------------------------------------------------------
_COLUMNS = [
    # Pre-filled by this script
    "txn_id",
    "txn_date",
    "account_id",
    "direction",
    "amount",
    "currency",
    "raw_description",
    "source_statement",
    # Left BLANK for the user to fill in Google Sheets
    "txn_type",
    "channel",
    "upi_type",
    "counterparty",
    "counterparty_category",
    "notes",
]


def _write_skeleton_csv(
    txns,
    source_statement: str,
    output_path: Path,
) -> int:
    """Write a skeleton benchmark CSV and return the row count."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()

        for txn in txns:
            writer.writerow({
                "txn_id":           txn.txn_id,
                "txn_date":         txn.txn_date.isoformat(),
                "account_id":       txn.account_id,
                "direction":        txn.direction.value,
                "amount":           str(txn.amount),
                "currency":         txn.currency,
                "raw_description":  txn.raw_description,
                "source_statement": source_statement,
                # Blank classification columns
                "txn_type":              "",
                "channel":               "",
                "upi_type":              "",
                "counterparty":          "",
                "counterparty_category": "",
                "notes":                 "",
            })

    return len(txns)


def export_hdfc_cc() -> None:
    """Parse the 1905 card (May–Dec 2025) and write the CC benchmark CSV."""
    print("── HDFC CC 1905 (May–Dec 2025) ───────────────────────────────────")

    cc_dir = config.DATA_DIR / "1905_CC"
    parser = HDFCCreditCardParser()

    # Filter to the chosen months by filename prefix (case-insensitive)
    selected_files = sorted(
        f for f in cc_dir.glob("*.csv")
        if f.name[:3].lower() in _CC_BENCHMARK_MONTHS
    )

    if not selected_files:
        print("  [ERROR] No matching CSV files found in", cc_dir)
        return

    print(f"  Parsing {len(selected_files)} files:")
    all_parsed = []
    for f in selected_files:
        rows = parser._parse_file(f)
        print(f"    {f.name:<60} → {len(rows)} rows")
        all_parsed.extend(rows)

    # Sort by txn_date so the CSV reads chronologically
    all_parsed.sort(key=lambda r: r.txn_date)

    canonical = transform(
        all_parsed,
        account_id="HDFC_CC_1905",
        currency="INR",
        source_statement="1905_CC",
    )

    out = OUTPUT_DIR / "benchmark_hdfc_cc.csv"
    count = _write_skeleton_csv(canonical, source_statement="1905_CC", output_path=out)
    print(f"\n  ✓ {count} rows → {out}\n")


def export_hdfc_cc_5778() -> None:
    """Parse ALL transactions on the 5778 card and write the benchmark CSV."""
    print("── HDFC CC 5778 (all transactions) ────────────────────────────────")

    cc_dir = config.DATA_DIR / "5778_CC"
    parser = HDFCCreditCardParser()

    all_files = sorted(cc_dir.glob("*.csv"))
    if not all_files:
        print("  [ERROR] No CSV files found in", cc_dir)
        return

    print(f"  Parsing {len(all_files)} files:")
    all_parsed = []
    for f in all_files:
        rows = parser._parse_file(f)
        print(f"    {f.name:<60} → {len(rows)} rows")
        all_parsed.extend(rows)

    all_parsed.sort(key=lambda r: r.txn_date)

    canonical = transform(
        all_parsed,
        account_id="HDFC_CC_5778",
        currency="INR",
        source_statement="5778_CC",
    )

    out = OUTPUT_DIR / "benchmark_hdfc_cc_5778.csv"
    count = _write_skeleton_csv(canonical, source_statement="5778_CC", output_path=out)
    print(f"\n  ✓ {count} rows → {out}\n")


def export_icici_savings() -> None:
    """Parse all ICICI savings transactions and write the ICICI benchmark CSV."""
    print("── ICICI Savings (full year, 106 transactions) ─────────────────")

    pdf_path = config.DATA_DIR / "ICICI_PDF_010125_311225.pdf"
    parser = ICICISavingsParser()

    parsed = parser.parse(pdf_path)
    print(f"  Parsed {len(parsed)} transactions from {pdf_path.name}")

    canonical = transform(
        parsed,
        account_id="ICICI_SAV_6118",
        currency="INR",
        source_statement="ICICI_PDF_010125_311225.pdf",
    )

    out = OUTPUT_DIR / "benchmark_icici_savings.csv"
    count = _write_skeleton_csv(
        canonical,
        source_statement="ICICI_PDF_010125_311225.pdf",
        output_path=out,
    )
    print(f"\n  ✓ {count} rows → {out}\n")


def main() -> None:
    print("Generating benchmark CSVs (parse + transform only — no rules, no LLM)\n")
    export_hdfc_cc()
    export_hdfc_cc_5778()
    export_icici_savings()
    print("Done.")
    print()
    print("Next steps:")
    print("  1. Import the CSVs into Google Sheets.")
    print("  2. Fill in: txn_type, channel, upi_type, counterparty,")
    print("     counterparty_category, notes for every row.")
    print("  3. Export back as CSVs → docs/personal-data/")
    print("  4. Run validation:")
    print("       python3 -m pipeline.run --source hdfc_cc_1905 --validate \\")
    print("           --benchmark docs/personal-data/benchmark_hdfc_cc.csv")
    print("       python3 -m pipeline.run --source hdfc_cc_5778 --validate \\")
    print("           --benchmark docs/personal-data/benchmark_hdfc_cc_5778.csv")
    print("       python3 -m pipeline.run --source icici_savings --validate \\")
    print("           --benchmark docs/personal-data/benchmark_icici_savings.csv")


if __name__ == "__main__":
    main()
