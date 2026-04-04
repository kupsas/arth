"""Tests for :mod:`api.services.holding_enrichment` (ETF sector heuristic, no live NSE)."""

from __future__ import annotations

from unittest.mock import MagicMock

from api.services.holding_enrichment import (
    EnrichmentReport,
    _apply_equity_sector_and_cap,
    is_listed_etf_nse_symbol,
)
from api.models import Holding
from pipeline.models import AssetClass, LiquidityClass, ValuationMethod


def test_is_listed_etf_nse_symbol() -> None:
    assert is_listed_etf_nse_symbol("GOLDIETF") is True
    assert is_listed_etf_nse_symbol("SILVERIETF") is True
    assert is_listed_etf_nse_symbol("GOLDBEES") is True
    assert is_listed_etf_nse_symbol("BHARTIARTL") is False


def test_apply_sets_etf_sector_without_calling_meta_for_etfs() -> None:
    nse = MagicMock()
    h = Holding(
        name="ICICI Gold ETF",
        account_platform="ICICI Direct",
        asset_class=AssetClass.EQUITY.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_1.value,
        symbol="GOLDIETF",
        user_id="sashank",
        is_active=True,
    )
    rep = EnrichmentReport()
    _apply_equity_sector_and_cap(
        h, nse, report=rep, throttle=False, last_call_ref=[0.0]
    )
    assert h.sector == "ETF"
    assert h.market_cap_class == "LARGE_CAP"
    nse.equityMetaInfo.assert_not_called()


def test_apply_calls_meta_for_non_etf() -> None:
    nse = MagicMock()
    nse.equityMetaInfo.return_value = {"industry": "TELECOMMUNICATIONS"}
    h = Holding(
        name="Bharti Airtel",
        account_platform="ICICI Direct",
        asset_class=AssetClass.EQUITY.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_1.value,
        symbol="BHARTIARTL",
        user_id="sashank",
        is_active=True,
    )
    rep = EnrichmentReport()
    _apply_equity_sector_and_cap(
        h, nse, report=rep, throttle=False, last_call_ref=[0.0]
    )
    assert h.sector == "TELECOMMUNICATIONS"
    nse.equityMetaInfo.assert_called_once()
