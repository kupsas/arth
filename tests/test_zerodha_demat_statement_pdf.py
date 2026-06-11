"""Zerodha Statement of Account text parsing (monthly demat email PDF)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from parsers.holdings.zerodha_demat_statement_pdf import parse_statement_of_account_text
from pipeline.models import AssetClass, InvestmentTxnType

# Snippet shaped like the real April 2026 statement (MF payout + equity delivery out).
_SAMPLE_SOA = """
Statement of Account from 2026-04-01 to 2026-04-30:
Date Transaction Description Buy/Cr Sell/Dr Balance
ISIN: INF179KC1BM8 Symbol: HDFC N50EWIF D-GROW
Opening balance: 5967.339
2026-04-16 NSE Payout 1208160065665564 1100001000012414 2627009 123.635 0.000 6090.974
Closing balance: 6090.974
ISIN: INF200K01SZ5 Symbol: SBI SAV DP GROWTH
Opening balance: 12464.057
2026-04-17 Delivery Out Instruction / BO Obligation 1208160065665564 1208160000000061 2026070 0.000 480.000 11984.057
Closing balance: 11984.057
Holdings as on 2026-04-30:
INE466L01038 360 ONE WAM-EQ1/- 4.000 4.000 0.000 1034.900 4139.600
"""


def _mock_amfi_lookup(isin: str, *, name_hint: str | None = None) -> dict | None:
    table = {
        "INF179KC1BM8": {
            "scheme_code": "151234",
            "scheme_name": "HDFC NIFTY 50 ETF FOF Direct Growth",
        },
        "INF200K01SZ5": {
            "scheme_code": "119551",
            "scheme_name": "SBI Savings Fund Direct Growth",
        },
    }
    return table.get(isin.upper())


@patch(
    "parsers.holdings.zerodha_demat_statement_pdf.lookup_isin_symbol",
    return_value=None,
)
@patch(
    "parsers.holdings.zerodha_demat_statement_pdf.lookup_amfi_scheme_by_isin",
    side_effect=lambda isin, **kw: _mock_amfi_lookup(isin, **kw),
)
def test_parse_statement_of_account_mf_payout_and_equity_delivery(
    _mock_amfi: object,
    _mock_nse: object,
) -> None:
    txns = parse_statement_of_account_text(_SAMPLE_SOA)
    assert len(txns) == 2

    mf = next(t for t in txns if t.metadata.get("isin") == "INF179KC1BM8")
    assert mf.txn_date == date(2026, 4, 16)
    assert mf.txn_type == InvestmentTxnType.BUY.value
    assert mf.quantity == pytest.approx(123.635)
    assert mf.notes == "NSE Payout"
    assert mf.account_platform == "Zerodha"
    assert mf.total_amount == 0.0
    assert mf.symbol == "151234"
    assert mf.metadata.get("asset_class") == AssetClass.MUTUAL_FUND.value
    assert mf.metadata.get("amfi_scheme_code") == "151234"

    sell = next(t for t in txns if t.metadata.get("isin") == "INF200K01SZ5")
    assert sell.txn_date == date(2026, 4, 17)
    assert sell.txn_type == InvestmentTxnType.SELL.value
    assert sell.quantity == pytest.approx(480.0)
    assert "Delivery Out" in (sell.notes or "")
    assert sell.symbol == "119551"
    assert sell.metadata.get("asset_class") == AssetClass.MUTUAL_FUND.value


def test_holdings_section_lines_are_not_parsed_as_transactions() -> None:
    txns = parse_statement_of_account_text(_SAMPLE_SOA)
    assert not any((t.metadata or {}).get("isin") == "INE466L01038" for t in txns)
