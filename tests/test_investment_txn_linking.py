"""Link investment_transactions → holdings (MF folio / AMFI / equity symbol)."""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.models import Holding, InvestmentTransaction  # noqa: E402
from pipeline.holding_parsers.base import ParsedInvestmentTxn  # noqa: E402
from pipeline.holding_parsers.nps import NPS_CANONICAL_HOLDING_NAME  # noqa: E402
from pipeline.investment_txn_linking import (  # noqa: E402
    find_holding_id_for_parsed_txn,
    find_holding_id_for_stored_txn,
    link_unlinked_investment_transactions,
    parse_mf_txn_notes,
)
from pipeline.holding_parsers.icici_direct_mf import parse_icici_direct_mf_path  # noqa: E402
from pipeline.holding_pipeline import ingest_holdings, ingest_investment_transactions  # noqa: E402
from pipeline.models import (  # noqa: E402
    AssetClass,
    InvestmentTxnType,
    LiquidityClass,
    ValuationMethod,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "holdings"


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


@pytest.fixture(name="session")
def _session(engine):
    with Session(engine) as s:
        yield s


def test_parse_mf_txn_notes_folio_and_name() -> None:
    folio, name = parse_mf_txn_notes("Foo Fund — Bar Scheme\nFolio 99999")
    assert folio == "99999"
    assert "Foo Fund" in (name or "")


def test_find_mf_parsed_txn_by_folio(session: Session) -> None:
    h = Holding(
        name="Quant — SMALL CAP",
        asset_class=AssetClass.MUTUAL_FUND.value,
        account_platform="ICICI Direct MF",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        user_id="u1",
        folio_number_encrypted="12345",
        quantity=10.5,
        current_value=1500.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    t = ParsedInvestmentTxn(
        txn_date=datetime.date(2024, 3, 10),
        name="Quant — SMALL CAP",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.5,
        price_per_unit=150.5,
        total_amount=1580.25,
        account_platform="ICICI Direct MF",
        metadata={"folio": "12345"},
    )
    assert find_holding_id_for_parsed_txn(session, "u1", t) == h.id
    assert find_holding_id_for_parsed_txn(session, "other", t) is None


def test_find_mf_parsed_txn_by_amfi_symbol(session: Session) -> None:
    h = Holding(
        name="SBI Large and Midcap Fund Regular Growth",
        symbol="103024",
        asset_class=AssetClass.MUTUAL_FUND.value,
        account_platform="ICICI Direct MF",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        user_id="u1",
        quantity=100.0,
        current_value=50_000.0,
    )
    session.add(h)
    session.commit()

    t = ParsedInvestmentTxn(
        txn_date=datetime.date(2024, 3, 10),
        symbol="103024",
        name="SBI — something",
        txn_type=InvestmentTxnType.SIP.value,
        quantity=1.0,
        price_per_unit=500.0,
        total_amount=500.0,
        account_platform="ICICI Direct MF",
        metadata={"folio": "", "amfi_scheme_code": "103024"},
    )
    assert find_holding_id_for_parsed_txn(session, "u1", t) == h.id


def test_find_equity_parsed_txn_by_symbol(session: Session) -> None:
    h = Holding(
        name="Reliance Industries",
        symbol="RELIANCE",
        asset_class=AssetClass.EQUITY.value,
        account_platform="ICICI Direct",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_1.value,
        user_id="u1",
        quantity=10.0,
        current_value=14_000.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    t = ParsedInvestmentTxn(
        txn_date=datetime.date(2024, 1, 2),
        symbol="RELIANCE",
        name=None,
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        price_per_unit=1400.0,
        total_amount=14_000.0,
        account_platform="ICICI Direct",
        metadata={},
    )
    assert find_holding_id_for_parsed_txn(session, "u1", t) == h.id


def test_find_ppf_parsed_txn_single_holding(session: Session) -> None:
    h = Holding(
        name="Public Provident Fund (PPF)",
        asset_class=AssetClass.PPF.value,
        account_platform="ICICI PPF",
        valuation_method=ValuationMethod.FIXED_RETURN.value,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        user_id="u1",
        current_value=51_500.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    t = ParsedInvestmentTxn(
        txn_date=datetime.date(2024, 4, 5),
        name="PPF contribution",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        price_per_unit=5000.0,
        total_amount=5000.0,
        account_platform="ICICI PPF",
        metadata={},
    )
    assert find_holding_id_for_parsed_txn(session, "u1", t) == h.id


def test_find_nps_parsed_txn_by_pran(session: Session) -> None:
    h = Holding(
        name=NPS_CANONICAL_HOLDING_NAME,
        asset_class=AssetClass.NPS.value,
        account_platform="NPS (CRA)",
        valuation_method=ValuationMethod.MANUAL.value,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        user_id="u1",
        current_value=20_000.0,
        account_identifier_encrypted="333333333333",
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    t = ParsedInvestmentTxn(
        txn_date=datetime.date(2023, 2, 14),
        name="NPS employee contribution",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        price_per_unit=50_000.0,
        total_amount=50_000.0,
        account_platform="NPS (CRA)",
        metadata={"pran": "333333333333"},
    )
    assert find_holding_id_for_parsed_txn(session, "u1", t) == h.id


def test_link_unlinked_backfills_stored_mf_txn(session: Session) -> None:
    h = Holding(
        name="Nippon India Small Cap Fund - Growth Plan (113177)",
        symbol="113177",
        asset_class=AssetClass.MUTUAL_FUND.value,
        account_platform="ICICI Direct MF",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        user_id="sashank",
        folio_number_encrypted="F777",
        quantity=100.0,
        current_value=15_000.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    notes = (
        "NIPPON INDIA MUTUAL FUND — Nippon India Small Cap Fund - Growth Plan (113177)\n"
        "Folio F777"
    )
    orphan = InvestmentTransaction(
        txn_date=datetime.date(2024, 6, 1),
        symbol="113177",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        price_per_unit=150.0,
        total_amount=1500.0,
        account_platform="ICICI Direct MF",
        holding_id=None,
        notes=notes,
    )
    session.add(orphan)
    session.commit()
    session.refresh(orphan)

    assert find_holding_id_for_stored_txn(session, "sashank", orphan) == h.id

    stats = link_unlinked_investment_transactions(session, user_ids=["sashank"])
    session.commit()

    assert stats["linked"] == 1
    session.refresh(orphan)
    assert orphan.holding_id == h.id


def test_link_unlinked_via_notes_amfi_only(session: Session) -> None:
    h = Holding(
        name="Some Fund (119551)",
        symbol="119551",
        asset_class=AssetClass.MUTUAL_FUND.value,
        account_platform="ICICI Direct MF",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        user_id="sashank",
        quantity=10.0,
        current_value=120.0,
    )
    session.add(h)
    session.commit()

    orphan = InvestmentTransaction(
        txn_date=datetime.date(2024, 1, 1),
        symbol=None,
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        price_per_unit=10.0,
        total_amount=100.0,
        account_platform="ICICI Direct MF",
        holding_id=None,
        notes="Foo (119551)\nFolio missing",
    )
    session.add(orphan)
    session.commit()

    stats = link_unlinked_investment_transactions(session, user_ids=["sashank"])
    session.commit()
    assert stats["linked"] == 1
    session.refresh(orphan)
    assert orphan.holding_id == h.id


def test_icici_mf_ingest_links_new_transactions(session: Session) -> None:
    path = _FIXTURES / "icici_mf_min.csv"
    holdings, txns = parse_icici_direct_mf_path(path)
    ingest_holdings(session, holdings, user_id="sashank", dry_run=False)
    stats = ingest_investment_transactions(
        session, txns, user_id="sashank", dry_run=False
    )
    assert stats["inserted"] == 1
    assert stats["linked_inline"] == 1
    rows = list(session.exec(select(InvestmentTransaction)).all())
    assert len(rows) == 1
    assert rows[0].holding_id is not None
