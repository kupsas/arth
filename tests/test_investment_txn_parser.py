"""
Phase A.6 — investment trade CSV parsing (ICICI annual export).

Detailed cases live in ``test_holding_parsers.py``; this module keeps a
dedicated entry point for the roadmap checklist.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.holding_parsers.icici_direct_equity import parse_annual_trade_csv
from pipeline.models import InvestmentTxnType

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "holdings"


def test_parse_annual_trade_csv_min_fixture() -> None:
    path = FIXTURES / "icici_annual_trade_min.csv"
    iso = {"INE646L01027": "INDIGO"}
    txns = parse_annual_trade_csv(path, iso)
    assert len(txns) >= 1
    assert any(t.txn_type == InvestmentTxnType.BUY.value for t in txns)
