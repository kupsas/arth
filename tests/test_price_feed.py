"""Phase A.6 — price_feed parsing helpers (no live NSE/AMFI in CI by default)."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.models import Holding, Price
from api.services.price_feed import (
    _bhav_symbol_to_close,
    backfill_prices,
    calendar_start_for_forced_nse_depth,
    latest_bhav_target_date,
    mf_scheme_codes_for_holdings,
    normalize_equity_symbol,
    parse_amfi_nav_rows,
    upsert_prices,
)
from pipeline.models import AssetClass, LiquidityClass, ValuationMethod


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


def test_calendar_start_for_forced_nse_depth() -> None:
    target = datetime.date(2025, 3, 21)  # Friday
    start = calendar_start_for_forced_nse_depth(
        target, depth_calendar_days=365, weekend_holiday_buffer_days=14
    )
    assert start == datetime.date(2024, 3, 7)


def test_mf_scheme_codes_for_holdings_filters_and_dedupes() -> None:
    h1 = Holding(
        name="A",
        account_platform="x",
        asset_class=AssetClass.MUTUAL_FUND.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        symbol="118551",
    )
    h2 = Holding(
        name="B",
        account_platform="x",
        asset_class=AssetClass.MUTUAL_FUND.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        symbol="118551",
    )
    h_eq = Holding(
        name="C",
        account_platform="x",
        asset_class=AssetClass.EQUITY.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_1.value,
        symbol="TCS",
    )
    assert mf_scheme_codes_for_holdings([h1, h2, h_eq]) == ["118551"]


def test_upsert_prices_idempotent_same_symbol_date() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    d = datetime.date(2025, 1, 1)
    with Session(engine) as session:
        upsert_prices(
            session,
            [Price(symbol="X", date=d, close_price=10.0, source="nse")],
        )
        upsert_prices(
            session,
            [Price(symbol="X", date=d, close_price=11.5, source="nse")],
        )
        session.commit()
        rows = list(session.exec(select(Price)).all())
    assert len(rows) == 1
    assert rows[0].close_price == pytest.approx(11.5)


@patch("api.services.price_feed.fetch_equity_prices_nse")
def test_backfill_prices_calls_fetch_and_upserts(mock_fetch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    d = datetime.date(2025, 2, 1)
    mock_fetch.return_value = [
        Price(symbol="INFY", date=d, close_price=1500.0, source="nse"),
    ]
    with Session(engine) as session:
        res = backfill_prices(session, "infy", d, d)
        session.commit()
    mock_fetch.assert_called_once()
    assert res["status"] == "ok"
    assert res["inserted"] == 1
    with Session(engine) as session:
        got = session.exec(select(Price).where(Price.symbol == "INFY")).first()
    assert got is not None
    assert got.close_price == pytest.approx(1500.0)
