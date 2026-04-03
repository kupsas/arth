"""Phase A.6 — price_feed parsing helpers (no live NSE/AMFI in CI by default)."""

from __future__ import annotations

import datetime
from typing import cast
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
    canonical_nse_symbol,
    latest_bhav_target_date,
    mf_scheme_codes_for_holdings,
    normalize_equity_symbol,
    parse_amfi_nav_rows,
    parse_amfi_navall,
    refresh_all_prices,
    upsert_prices,
)
from pipeline.models import AssetClass, LiquidityClass, ValuationMethod


def test_normalize_equity_symbol_strips_suffix() -> None:
    assert normalize_equity_symbol("  reliance.ns ") == "RELIANCE"
    assert normalize_equity_symbol("TCS.NSE") == "TCS"


def test_canonical_nse_symbol_maps_icici_legacy_codes() -> None:
    assert canonical_nse_symbol("APOTYR") == "APOLLOTYRE"
    assert canonical_nse_symbol("indoil") == "IOC"
    assert canonical_nse_symbol("RELIANCE") == "RELIANCE"


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


def test_parse_amfi_navall_attaches_category_and_amc() -> None:
    """Section headers in NAVAll apply to following scheme rows until the next header."""
    text = """
Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

SBI Mutual Fund

103024;INF200K01305;;SBI LARGE & MIDCAP FUND;100.5000;15-Mar-2025
"""
    latest, meta = parse_amfi_navall(text)
    assert latest["103024"][0] == pytest.approx(100.5)
    cat, house = meta["103024"]
    assert cat is not None and "Large Cap" in cat
    assert house == "SBI Mutual Fund"


def test_parse_amfi_nav_rows_legacy_wide_row_date_in_last_column() -> None:
    """Older NAVAll-style rows can have extra empty columns; date may sit at the end."""
    text = """
Scheme Code;Scheme Name;ISIN Div Payout;ISIN Reinvest;Net Asset Value;;Date
103024;SBI FUND;INF200K01305;;538.9405;;;03-Mar-2025
"""
    m = parse_amfi_nav_rows(text)
    assert "103024" in m
    nav, d = m["103024"]
    assert nav == pytest.approx(538.9405)
    assert d == datetime.date(2025, 3, 3)


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


@patch("api.services.price_feed.fetch_equity_closes_from_nse_bhav", return_value={})
@patch(
    "api.services.price_feed.latest_bhav_target_date",
    return_value=datetime.date(2025, 3, 24),
)
def test_refresh_all_prices_db_fallback_when_bhav_empty(
    _mock_target: object, _mock_bhav: object
) -> None:
    """If NSE returns no row for the session, use the latest ``prices`` row on or before target."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    d_hist = datetime.date(2025, 3, 20)
    with Session(engine) as session:
        session.add(
            Price(
                symbol="APOLLOTYRE",
                date=d_hist,
                close_price=400.0,
                source="nse",
            )
        )
        h = Holding(
            name="Apollo Tyres",
            account_platform="ICICI Direct",
            asset_class=AssetClass.EQUITY.value,
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            symbol="APOTYR",
            quantity=2.0,
            user_id="sashank",
        )
        session.add(h)
        session.commit()
        session.refresh(h)
        hid = h.id

        out = refresh_all_prices(session, user_id="sashank")
        session.commit()

    assert int(cast(int, out["holdings_updated"])) >= 1
    with Session(engine) as session:
        h2 = session.exec(select(Holding).where(Holding.id == hid)).first()
        assert h2 is not None
        assert h2.current_price_per_unit == pytest.approx(400.0)
        assert h2.last_valued_date == d_hist
        assert h2.current_value == pytest.approx(800.0)
