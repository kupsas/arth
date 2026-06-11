"""Tests for ``pipeline.amfi_isin_map`` (NAVAll ISIN index)."""

from __future__ import annotations

import datetime

from pipeline.amfi_isin_map import build_isin_to_scheme_map


def test_build_isin_to_scheme_map_indexes_growth_and_reinvest_isins() -> None:
    text = """
Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date
119551;INF204K016L0;INF204K016L0;Foo Fund;12.345;15-Mar-2025
103024;INF200K01305;;SBI LARGE & MIDCAP FUND;100.5000;15-Mar-2025
"""
    m = build_isin_to_scheme_map(text)
    assert m["INF204K016L0"]["scheme_code"] == "119551"
    assert m["INF204K016L0"]["scheme_name"] == "Foo Fund"
    assert m["INF200K01305"]["scheme_code"] == "103024"
    assert m["INF200K01305"]["nav"] == 100.5
    assert m["INF200K01305"]["nav_date"] == datetime.date(2025, 3, 15).isoformat()
