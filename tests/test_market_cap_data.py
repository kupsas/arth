"""Market cap bucket map (defaults + optional JSON overrides)."""

from __future__ import annotations

from pipeline.market_cap_data import (
    DEFAULT_NSE_MARKET_CAP,
    market_cap_for_symbol,
    merged_market_cap_map,
)


def test_merged_map_includes_defaults() -> None:
    m = merged_market_cap_map()
    assert m["RELIANCE"] == "LARGE_CAP"
    assert len(m) >= len(DEFAULT_NSE_MARKET_CAP)


def test_market_cap_for_symbol_unknown() -> None:
    assert market_cap_for_symbol("NOTALISTEDXYZ999") is None
