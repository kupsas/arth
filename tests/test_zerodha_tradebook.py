"""Zerodha tradebook CSV parser (backup upload path)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from parsers.holdings.zerodha_tradebook import (
    ZerodhaTradebookParser,
    aggregate_zerodha_trades,
    parse_zerodha_tradebook_path,
)
from pipeline.models import InvestmentTxnType

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "holdings" / "zerodha_tradebook_min.csv"
SAMPLE_CSV = (
    Path(__file__).resolve().parent / "fixtures" / "holdings" / "zerodha_tradebook_sample.csv"
)


def test_detect_zerodha_tradebook() -> None:
    hit = ZerodhaTradebookParser.detect(FIXTURE)
    assert hit is not None
    assert hit.source_type == "zerodha_tradebook"


def test_parse_min_fixture_aggregates_buy_legs() -> None:
    holdings, txns = parse_zerodha_tradebook_path(FIXTURE)
    assert len(txns) == 2
    buy = next(t for t in txns if t.txn_type == InvestmentTxnType.BUY.value)
    assert buy.txn_date == date(2025, 5, 2)
    assert buy.symbol == "RELIANCE"
    assert buy.quantity == pytest.approx(8.0)
    assert buy.total_amount == pytest.approx(5 * 1508.5 + 3 * 1509.0)
    assert buy.account_platform == "Zerodha"
    assert (buy.metadata or {}).get("isin") == "INE002A01018"
    assert len(holdings) == 1
    assert holdings[0].quantity == pytest.approx(4.0)


def test_aggregate_zerodha_trades_buckets_by_isin_when_no_symbol() -> None:
    from parsers.holdings.base import ParsedInvestmentTxn

    legs = [
        ParsedInvestmentTxn(
            txn_date=date(2025, 1, 1),
            symbol=None,
            name="X",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=2,
            price_per_unit=10,
            total_amount=20,
            account_platform="Zerodha",
            metadata={"isin": "INE002A01018"},
        ),
        ParsedInvestmentTxn(
            txn_date=date(2025, 1, 1),
            symbol=None,
            name="X",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=3,
            price_per_unit=10,
            total_amount=30,
            account_platform="Zerodha",
            metadata={"isin": "INE002A01018"},
        ),
    ]
    out = aggregate_zerodha_trades(legs)
    assert len(out) == 1
    assert out[0].quantity == pytest.approx(5.0)
    assert out[0].total_amount == pytest.approx(50.0)


def test_parse_sample_tradebook_csv_aggregates_and_derives_holdings() -> None:
    """Committed synthetic tradebook (no personal-data path)."""
    holdings, txns = parse_zerodha_tradebook_path(SAMPLE_CSV)
    assert len(txns) == 5  # 7 legs → 5 aggregated (date, side, symbol) buckets
    assert all(t.account_platform == "Zerodha" for t in txns)
    assert all(t.price_per_unit > 0 for t in txns)
    assert len(holdings) == 3
    ztesta = next(t for t in txns if t.symbol == "ZTESTA" and t.txn_type == InvestmentTxnType.BUY.value)
    assert ztesta.quantity == pytest.approx(15.0)
    assert ztesta.total_amount == pytest.approx(10 * 100.0 + 5 * 101.0)
