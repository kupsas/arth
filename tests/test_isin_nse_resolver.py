"""ISIN → NSE symbol fallback via bhav map (cached)."""

from __future__ import annotations

import pytest

from pipeline import isin_nse_resolver


def test_lookup_isin_invalid_returns_none() -> None:
    assert isin_nse_resolver.lookup_isin_from_nse_bhav("") is None
    assert isin_nse_resolver.lookup_isin_from_nse_bhav("FOO") is None
    assert isin_nse_resolver.lookup_isin_from_nse_bhav("INE002A0101") is None  # short


def test_lookup_isin_uses_merged_bhav_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        isin_nse_resolver,
        "_merged_bhav_isin_map",
        lambda: {"INE002A01018": "RELIANCE"},
    )
    assert isin_nse_resolver.lookup_isin_from_nse_bhav("ine002a01018") == "RELIANCE"
