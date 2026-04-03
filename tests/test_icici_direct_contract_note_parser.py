"""Unit tests for :mod:`pipeline.holding_parsers.icici_direct_contract_note` (NSE trades PDF)."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.holding_parsers.icici_direct_contract_note import (
    aggregate_icici_direct_trades,
    parse_nse_capital_market_tables,
    parse_nse_executed_text,
)
from pipeline.holding_parsers.base import ParsedInvestmentTxn
from pipeline.models import InvestmentTxnType


def test_parse_nse_capital_market_table_via_pdfplumber(tmp_path: Path) -> None:
    """NSE *trade_details* PDFs use B/S and Trade No — see live ``nse-direct`` layout."""
    header = [
        "Sr.\nNo",
        "TM Name",
        "Client\nCode",
        "Buy/\nSell",
        "Name of the Security",
        "Symbol",
        "Series",
        "Trade No",
        "Trade Time",
        "Quantity",
        "Price\n(Rs.)",
        "Traded Value\n(Rs.)",
    ]
    row = [
        "1",
        "X\nLTD",
        "8505362771",
        "S",
        "Tata Motors Limited",
        "TATAMOTORS",
        "EQ",
        "20250902601072092",
        "09:37:05 AM",
        "41",
        "686.25",
        "28136.25",
    ]
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 minimal")

    fake_page = MagicMock()
    fake_page.extract_tables.return_value = [[header, row]]
    fake_pdf = MagicMock()
    fake_pdf.__enter__.return_value = fake_pdf
    fake_pdf.__exit__.return_value = None
    fake_pdf.pages = [fake_page]

    with patch(
        "pipeline.holding_parsers.icici_direct_contract_note.pdfplumber.open",
        return_value=fake_pdf,
    ):
        out = parse_nse_capital_market_tables(
            pdf_path,
            doc_trade_date=None,
            fallback_trade_date=datetime.date(2025, 9, 2),
            trade_date_source="email_received_date",
            extra_metadata={},
        )
    assert len(out) == 1
    assert out[0].symbol == "TATAMOTORS"
    assert out[0].txn_type == InvestmentTxnType.SELL.value
    assert out[0].quantity == 41.0
    assert out[0].txn_date == datetime.date(2025, 9, 2)
    assert (out[0].metadata or {}).get("nse_trade_no") == "20250902601072092"


def test_parse_nse_executed_buy() -> None:
    d = datetime.date(2024, 3, 15)
    text = """
    Trade Date: 15-03-2024
    RELIANCE BUY 10 2,450.50
    TATAPOWER SELL 5 400.00
    """
    rows = parse_nse_executed_text(
        text,
        trade_date=d,
        trade_date_source="pdf_header",
        extra_metadata={"ingest_source": "nse_trades_executed_pdf"},
    )
    assert len(rows) == 2
    assert rows[0].symbol == "RELIANCE"
    assert rows[0].txn_type == InvestmentTxnType.BUY.value
    assert rows[0].quantity == 10
    assert rows[0].price_per_unit == 2450.50
    assert rows[0].metadata.get("nse_symbol_raw") == "RELIANCE"
    assert rows[0].metadata.get("trade_date_source") == "pdf_header"
    assert rows[0].metadata.get("ingest_source") == "nse_trades_executed_pdf"
    assert rows[1].txn_type == InvestmentTxnType.SELL.value


def test_classify_subject_nse_only() -> None:
    from scraper.email_parsers.icici_direct_trade import classify_icici_direct_subject

    assert classify_icici_direct_subject("Your Trades executed at NSE on 15-03-2024") == "nse_trades_executed"


def test_classify_subject_rejects_order_and_contract() -> None:
    from scraper.email_parsers.icici_direct_trade import classify_icici_direct_subject

    assert classify_icici_direct_subject("Order and Trade confirmations …") is None
    assert classify_icici_direct_subject("NSE Equity Digital Contract Note for your trades") is None


def test_aggregate_four_legs_into_one() -> None:
    """Split PDF lines for the same NSE symbol → one CSV-style row."""
    d = datetime.date(2025, 9, 2)
    legs = [
        ParsedInvestmentTxn(
            txn_date=d,
            symbol="TATAMOTORS",
            name="x",
            txn_type=InvestmentTxnType.SELL.value,
            quantity=41.0,
            price_per_unit=686.25,
            total_amount=41 * 686.25,
            account_platform="ICICI Direct",
            metadata={},
        ),
        ParsedInvestmentTxn(
            txn_date=d,
            symbol="TATAMOTORS",
            name="x",
            txn_type=InvestmentTxnType.SELL.value,
            quantity=29.0,
            price_per_unit=686.25,
            total_amount=29 * 686.25,
            account_platform="ICICI Direct",
            metadata={},
        ),
    ]
    out = aggregate_icici_direct_trades(legs)
    assert len(out) == 1
    assert out[0].quantity == 70.0
    assert abs(out[0].total_amount - 48037.5) < 0.02
    assert abs(out[0].price_per_unit - 686.25) < 0.01
