"""Zerodha demat: derive equity + MF holdings from mixed ledger rows."""

from __future__ import annotations

from datetime import date

from parsers.holdings.zerodha_demat_statement_pdf import derive_zerodha_holdings
from parsers.holdings.base import ParsedInvestmentTxn
from pipeline.models import AssetClass, InvestmentTxnType


def test_derive_zerodha_holdings_splits_equity_and_mf() -> None:
    txns = [
        ParsedInvestmentTxn(
            txn_date=date(2026, 4, 10),
            symbol="RELIANCE",
            name="Reliance",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=5.0,
            price_per_unit=0.0,
            total_amount=0.0,
            account_platform="Zerodha",
            metadata={"asset_class": AssetClass.EQUITY.value, "isin": "INE002A01018"},
        ),
        ParsedInvestmentTxn(
            txn_date=date(2026, 4, 16),
            symbol="103024",
            name="SBI LARGE & MIDCAP FUND",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=10.0,
            price_per_unit=0.0,
            total_amount=0.0,
            account_platform="Zerodha",
            metadata={
                "asset_class": AssetClass.MUTUAL_FUND.value,
                "amfi_scheme_code": "103024",
                "isin": "INF200K01305",
            },
        ),
    ]
    holdings = derive_zerodha_holdings(txns)
    assert len(holdings) == 2
    by_class = {h.asset_class: h for h in holdings}
    assert by_class[AssetClass.EQUITY.value].symbol == "RELIANCE"
    assert by_class[AssetClass.EQUITY.value].account_platform == "Zerodha"
    assert by_class[AssetClass.MUTUAL_FUND.value].symbol == "103024"
    assert by_class[AssetClass.MUTUAL_FUND.value].amfi_scheme_code == "103024"
