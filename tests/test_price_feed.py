"""Phase A.6 — price_feed parsing helpers (no live NSE/AMFI in CI by default)."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from api.services.price_feed import (
    _bhav_symbol_to_close,
    latest_bhav_target_date,
    normalize_equity_symbol,
    parse_amfi_nav_rows,
)


def test_normalize_equity_symbol_strips_suffix() -> None:
    assert normalize_equity_symbol("  reliance.ns ") == "RELIANCE"
    assert normalize_equity_symbol("TCS.NSE") == "TCS"


def test_latest_bhav_target_date_weekday_unchanged() -> None:
    # 2025-03-24 is a Monday — NSE session day, should stay put.
    mon = datetime.date(2025, 3, 24)
    assert latest_bhav_target_date(as_of=mon) == mon


def test_latest_bhav_target_date_saturday_rolls_to_friday() -> None:
    sat = datetime.date(2025, 3, 22)  # Saturday
    assert latest_bhav_target_date(as_of=sat) == datetime.date(2025, 3, 21)


def test_parse_amfi_nav_rows_picks_latest_per_code() -> None:
    text = """
Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date
119551;INF204K016L0;INF204K016L0;Foo Fund;12.345;15-Mar-2025
119551;INF204K016L0;INF204K016L0;Foo Fund;13.000;18-Mar-2025
"""
    m = parse_amfi_nav_rows(text)
    assert "119551" in m
    nav, d = m["119551"]
    assert nav == pytest.approx(13.0)
    assert d == datetime.date(2025, 3, 18)


def test_bhav_symbol_to_close_udiff_header(tmp_path: Path) -> None:
    p = tmp_path / "bhav.csv"
    p.write_text(
        "TckrSymb,ClsPric,ignored\n"
        "RELIANCE,2450.5,x\n"
        "TCS,3200,x\n",
        encoding="utf-8",
    )
    m = _bhav_symbol_to_close(p)
    assert m["RELIANCE"] == pytest.approx(2450.5)
    assert m["TCS"] == pytest.approx(3200.0)


def test_bhav_symbol_to_close_legacy_header(tmp_path: Path) -> None:
    p = tmp_path / "legacy.csv"
    p.write_text(
        "SYMBOL,CLOSE\n"
        "INFY,1500\n",
        encoding="utf-8",
    )
    m = _bhav_symbol_to_close(p)
    assert m["INFY"] == pytest.approx(1500.0)
