"""Tests for Phase A.2 holding / investment parsers and ingest helpers."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Fernet-backed columns require a key before importing ORM models.
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.models import Holding  # noqa: E402
from pipeline.holding_parsers.base import parse_icici_number  # noqa: E402
from pipeline.holding_parsers.icici_direct_equity import (  # noqa: E402
    parse_annual_trade_csv,
    parse_icici_direct_equity_dir,
    parse_portfolio_summary_csv,
    resolve_icici_direct_nse_symbol,
)
from pipeline.holding_parsers.icici_direct_mf import parse_icici_direct_mf_path  # noqa: E402
from pipeline.holding_parsers.icici_ppf import parse_icici_ppf_csv  # noqa: E402
from pipeline.holding_parsers.liabilities import parse_bike_loan_txt  # noqa: E402
from pipeline.holding_parsers.nps import (  # noqa: E402
    NPS_CANONICAL_HOLDING_NAME,
    parse_nps_statement,
)
from pipeline.holding_pipeline import ingest_holdings, validate_parsed_holding  # noqa: E402
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "holdings"


def test_parse_icici_number_paren_and_spaced_minus() -> None:
    assert parse_icici_number("(8327.67)") == pytest.approx(-8327.67)
    assert parse_icici_number("- 4.91") == pytest.approx(-4.91)
    assert parse_icici_number("50,000.00") == pytest.approx(50000.0)


def test_icici_portfolio_summary_maps_isin_to_nse() -> None:
    path = FIXTURES / "icici_portfolio_summary_min.csv"
    holdings, iso = parse_portfolio_summary_csv(path)
    assert "INE646L01027" in iso
    indigo = next(h for h in holdings if h.symbol == "INDIGO")
    assert indigo.quantity == 10
    assert indigo.asset_class == AssetClass.EQUITY.value
    sto = next(h for h in holdings if "STONE" in h.name.upper())
    assert sto.valuation_method == ValuationMethod.MANUAL.value
    assert sto.notes and "SEBI" in sto.notes


def test_icici_annual_trades_use_isin_map() -> None:
    path = FIXTURES / "icici_annual_trade_min.csv"
    iso = {"INE646L01027": "INDIGO"}
    txns = parse_annual_trade_csv(path, iso)
    assert len(txns) == 2
    buy = txns[0]
    assert buy.txn_type == InvestmentTxnType.BUY.value
    assert buy.symbol == "INDIGO"
    sell = txns[1]
    assert sell.symbol == "HDFCBANK"


def test_icici_equity_directory_merge() -> None:
    d = FIXTURES / "icici_equity_dir"
    h, t = parse_icici_direct_equity_dir(d)
    assert len(h) >= 2
    assert len(t) >= 2


def test_icici_mf_skips_rejected_and_derives_holding() -> None:
    path = FIXTURES / "icici_mf_min.csv"
    holdings, txns = parse_icici_direct_mf_path(path)
    assert len(txns) == 1
    assert txns[0].quantity == pytest.approx(10.5)
    assert len(holdings) == 1
    assert holdings[0].quantity == pytest.approx(10.5)


def test_icici_ppf_deposit_and_interest() -> None:
    path = FIXTURES / "icici_ppf_min.csv"
    holdings, txns = parse_icici_ppf_csv(path)
    assert len(holdings) == 1
    assert holdings[0].asset_class == AssetClass.PPF.value
    # First BUY 05-Apr-2020 → FY end 31-Mar-2021 → statutory maturity +15y
    assert holdings[0].maturity_date == date(2036, 3, 31)
    assert holdings[0].principal_amount == pytest.approx(50000.0)
    types = {t.txn_type for t in txns}
    assert InvestmentTxnType.BUY.value in types
    assert InvestmentTxnType.DIVIDEND.value in types


def test_nps_pran_and_schemes() -> None:
    path = FIXTURES / "nps_min.csv"
    holdings, txns = parse_nps_statement(path)
    assert len(holdings) == 1
    assert holdings[0].name == NPS_CANONICAL_HOLDING_NAME
    assert holdings[0].valuation_method == ValuationMethod.MANUAL.value
    assert holdings[0].current_value == pytest.approx(150_000.0)
    assert not txns


def test_nps_contribution_section_employee_buy() -> None:
    path = FIXTURES / "nps_contribution_section.csv"
    holdings, txns = parse_nps_statement(path, reference_date=date(2026, 12, 31))
    contrib = [t for t in txns if t.name == "NPS employee contribution"]
    assert len(contrib) == 2
    assert sum(t.total_amount for t in contrib) == pytest.approx(50228.60)
    assert len(holdings) == 1
    assert holdings[0].name == NPS_CANONICAL_HOLDING_NAME
    assert holdings[0].current_value == pytest.approx(100_000.0)
    assert holdings[0].principal_amount == pytest.approx(50228.60)


def test_nps_as_of_phrase_also_sets_snapshot_metadata() -> None:
    """Some CRA exports say 'as of' instead of 'as on' — same date pattern."""
    from pipeline.holding_parsers.nps import _statement_as_on_max

    lines = [
        "Header",
        "Value of your Holdings as of March 15 2025 (in Rs).",
        "E,100000.00,500.00,200.00",
    ]
    assert _statement_as_on_max(lines) == date(2025, 3, 15)


def test_nps_glued_as_on_row_sets_snapshot_metadata() -> None:
    """CRA often glues ')as on Month D YYYY' to the previous field with no space."""
    path = FIXTURES / "nps_glued_as_on.csv"
    holdings, txns = parse_nps_statement(path, reference_date=date(2027, 1, 1))
    assert len(holdings) == 1
    assert not txns
    assert holdings[0].metadata.get("value_as_of_date") == "2026-03-23"
    assert holdings[0].current_value == pytest.approx(150_000.0)


def test_nps_skips_holdings_when_statement_as_of_is_in_the_future() -> None:
    path = FIXTURES / "nps_future_asof.csv"
    holdings, txns = parse_nps_statement(path, reference_date=date(2026, 1, 1))
    assert len(holdings) == 0
    assert not txns


def test_bike_loan_txt() -> None:
    rows = parse_bike_loan_txt(FIXTURES / "bike_loan_min.txt")
    assert len(rows) == 1
    assert rows[0].emi_amount == pytest.approx(3500.0)
    assert rows[0].tenure_remaining_months == 48


@pytest.fixture(name="engine")
def _engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)
    eng.dispose()


def test_ingest_holdings_round_trip_encrypted_folio(engine) -> None:
    from pipeline.holding_parsers.base import ParsedHolding

    ph = ParsedHolding(
        name="Quant SMALL CAP",
        asset_class=AssetClass.MUTUAL_FUND.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        account_platform="ICICI Direct MF",
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        quantity=10.5,
        current_value=1580.25,
        folio_number="12345",
    )
    assert not validate_parsed_holding(ph)
    with Session(engine) as session:
        stats = ingest_holdings(session, [ph], user_id="tester", dry_run=False)
        assert stats["inserted"] == 1
        from sqlmodel import select

        row = session.exec(select(Holding).where(Holding.name == ph.name)).first()
        assert row is not None
        assert row.folio_number_encrypted == "12345"


def test_resolve_icici_direct_nse_symbol_isin_bhav_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When static ISIN map misses, :func:`resolve_icici_direct_nse_symbol` uses bhav lookup."""
    monkeypatch.setattr(
        "pipeline.isin_nse_resolver.lookup_isin_from_nse_bhav",
        lambda isin: "NEWSYM" if isin == "INE999Z01099" else None,
    )
    assert resolve_icici_direct_nse_symbol(isin="INE999Z01099", icici_short="UNKNOWN") == "NEWSYM"
