#!/usr/bin/env python3
"""
Summarise Zerodha Console tradebook CSV parsing (manual upload path).

Usage:
    python3 scripts/zerodha_tradebook_probe.py
    python3 scripts/zerodha_tradebook_probe.py path/to/tradebook.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from parsers.holdings.base import strip_bom  # noqa: E402
from parsers.holdings.zerodha_tradebook import (  # noqa: E402
    _parse_tradebook_csv,
    aggregate_zerodha_trades,
    parse_zerodha_tradebook_path,
)

DEFAULT_CSV = (
    REPO_ROOT / "tests" / "fixtures" / "holdings" / "zerodha_tradebook_sample.csv"
)


def _count_csv_rows(path: Path) -> int:
    text = strip_bom(path.read_text(encoding="utf-8", errors="replace"))
    reader = csv.DictReader(line.strip() for line in text.splitlines() if line.strip())
    return sum(1 for _ in reader)


def main() -> int:
    p = argparse.ArgumentParser(description="Probe Zerodha tradebook CSV parse quality.")
    p.add_argument(
        "csv_path",
        nargs="?",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Tradebook CSV (default: {DEFAULT_CSV.relative_to(REPO_ROOT)})",
    )
    args = p.parse_args()
    path = args.csv_path.resolve()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    csv_rows = _count_csv_rows(path)
    legs = _parse_tradebook_csv(path)
    txns = aggregate_zerodha_trades(legs)
    holdings, txns_full = parse_zerodha_tradebook_path(path)

    skipped = csv_rows - len(legs)
    types = Counter(t.txn_type for t in txns)
    segments = Counter((t.metadata or {}).get("segment") for t in legs)
    zero_price = sum(1 for t in txns if t.price_per_unit <= 0)

    print(f"File: {path.name}")
    print(f"CSV data rows:     {csv_rows}")
    print(f"Parsed legs:       {len(legs)}  (skipped {skipped})")
    print(f"After aggregation: {len(txns)}")
    print(f"Derived holdings:  {len(holdings)}")
    print(f"Txn types:         {dict(types)}")
    print(f"Segments (legs):   {dict(segments)}")
    print(f"Zero-price txns:   {zero_price}")
    if txns:
        dates = sorted(t.txn_date for t in txns)
        print(f"Date range:        {dates[0]} → {dates[-1]}")
        sample = txns[0]
        print(
            f"Sample row:        {sample.txn_date} {sample.txn_type} "
            f"{sample.symbol} qty={sample.quantity} "
            f"ppu={sample.price_per_unit} total={sample.total_amount} "
            f"price_source={(sample.metadata or {}).get('price_source')}"
        )

    print()
    print("Notes:")
    print("  • Tradebook is equity-only (EQ); MF demat legs come from monthly demat email PDF.")
    print("  • Aggregation merges same-day buy/sell fills per symbol — per-fill IDs are not kept.")
    print("  • Prices are execution prices from CSV (price_source=statement); no NSE bhav proxy here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
