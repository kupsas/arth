"""Zerodha demat SOA market-price backfill (NSE bhav + AMFI NAV)."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from parsers.holdings.base import ParsedInvestmentTxn
from parsers.holdings.zerodha_demat_pricing import (
    _DematPriceContext,
    apply_market_prices_to_zerodha_demat_txns,
)
from pipeline.models import AssetClass, InvestmentTxnType


def _txn(**kwargs: object) -> ParsedInvestmentTxn:
    defaults = {
        "txn_date": datetime.date(2022, 4, 5),
        "symbol": "CDSL",
        "name": "CDSL",
        "txn_type": InvestmentTxnType.BUY.value,
        "quantity": 1.0,
        "price_per_unit": 0.0,
        "total_amount": 0.0,
        "account_platform": "Zerodha",
        "metadata": {
            "kind": "zerodha_demat_statement_pdf",
            "asset_class": AssetClass.EQUITY.value,
            "isin": "INE736A01011",
        },
    }
    defaults.update(kwargs)
    return ParsedInvestmentTxn(**defaults)  # type: ignore[arg-type]


@patch(
    "parsers.holdings.zerodha_demat_pricing.load_nse_equity_bhav_map_cached_first",
)
def test_equity_leg_gets_nse_bhav_close(mock_bhav: object) -> None:
    mock_bhav.return_value = {f"SYM{i}": 1.0 for i in range(201)} | {"CDSL": 760.67}
    out = apply_market_prices_to_zerodha_demat_txns([_txn()])
    assert len(out) == 1
    t = out[0]
    assert t.price_per_unit == pytest.approx(760.67)
    assert t.total_amount == pytest.approx(760.67)
    assert t.metadata.get("price_source") == "nse_bhav"
    assert t.metadata.get("price_session_date") == "2022-04-05"


@patch("parsers.holdings.zerodha_demat_pricing.fetch_mf_nav_history")
def test_mf_leg_gets_amfi_nav_from_history(mock_mf: object) -> None:
    from api.models import Price

    mock_mf.return_value = [
        Price(
            symbol="149107",
            date=datetime.date(2022, 4, 6),
            close_price=11.0349,
            source="mfapi",
        )
    ]
    t = _txn(
        txn_date=datetime.date(2022, 4, 6),
        symbol="149107",
        name="HDFC N50 EW",
        quantity=339.798,
        metadata={
            "kind": "zerodha_demat_statement_pdf",
            "asset_class": AssetClass.MUTUAL_FUND.value,
            "amfi_scheme_code": "149107",
            "isin": "INF179KC1BM8",
        },
    )
    out = apply_market_prices_to_zerodha_demat_txns([t])
    assert out[0].price_per_unit == pytest.approx(11.0349)
    assert out[0].total_amount == pytest.approx(round(339.798 * 11.0349, 2))
    assert out[0].metadata.get("price_source") == "amfi_nav"


@pytest.mark.skipif(
    not __import__("pathlib").Path("data/.nse_cache/cm05APR2022bhav.csv").is_file(),
    reason="Local NSE cache not present",
)
def test_equity_leg_uses_local_bhav_cache_when_available() -> None:
    """Integration: real cm05APR2022bhav.csv in data/.nse_cache."""
    out = apply_market_prices_to_zerodha_demat_txns(
        [_txn()],
        ctx=_DematPriceContext(),
    )
    t = out[0]
    assert t.price_per_unit > 0
    assert t.total_amount == pytest.approx(t.price_per_unit * t.quantity, rel=1e-6)
    assert t.metadata.get("price_source") == "nse_bhav"


@patch("parsers.holdings.zerodha_demat_pricing.fetch_mf_nav_history")
def test_mf_payout_uses_previous_business_day_nav(mock_mf: object) -> None:
    """NSE Payout on Wednesday uses Tuesday NAV (T-1)."""
    from api.models import Price

    mock_mf.return_value = [
        Price(
            symbol="149107",
            date=datetime.date(2022, 4, 5),
            close_price=11.0652,
            source="amfi_portal",
        ),
        Price(
            symbol="149107",
            date=datetime.date(2022, 4, 6),
            close_price=11.0349,
            source="amfi_portal",
        ),
    ]
    t = _txn(
        txn_date=datetime.date(2022, 4, 6),
        symbol="149107",
        name="HDFC N50 EW",
        quantity=100.0,
        notes="NSE Payout",
        metadata={
            "kind": "zerodha_demat_statement_pdf",
            "asset_class": AssetClass.MUTUAL_FUND.value,
            "amfi_scheme_code": "149107",
            "demat_description": "NSE Payout",
            "isin": "INF179KC1BM8",
        },
    )
    out = apply_market_prices_to_zerodha_demat_txns([t])
    assert out[0].price_per_unit == pytest.approx(11.0652)
    assert out[0].metadata.get("price_session_date") == "2022-04-05"
