"""Tests for :func:`pipeline.holding_pipeline.investment_txn_exists` (email vs CSV dedupe)."""

from __future__ import annotations

import datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import os

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.models import InvestmentTransaction  # noqa: E402
from parsers.holdings.base import ParsedInvestmentTxn  # noqa: E402
from pipeline.holding_pipeline import (  # noqa: E402
    PRICE_SOURCE_STATEMENT,
    ingest_investment_transactions,
    investment_txn_exists,
)
from pipeline.models import InvestmentTxnType  # noqa: E402


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


def test_equity_duplicate_ignores_price_per_unit_drift(session: Session) -> None:
    """Email used full precision PPU; CSV rounds — still one logical trade."""
    d = datetime.date(2023, 8, 18)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol="VGUARD",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=151.0,
            price_per_unit=313.584106,
            total_amount=47351.2,
            account_platform="ICICI Direct",
            notes="from email",
            source_type="email",
        )
    )
    session.commit()

    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol="VGUARD",
        name="VGUARD",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=151.0,
        price_per_unit=313.58,
        total_amount=47351.2,
        account_platform="ICICI Direct",
        notes="CSV",
    )
    assert investment_txn_exists(session, parsed) is True


def test_mf_duplicate_different_scheme_wording_same_folio(session: Session) -> None:
    """Gmail vs CSV use different scheme strings; folio + token bag align."""
    d = datetime.date(2024, 6, 9)
    email_notes = (
        "Quant Money Managers Ltd — Quant ELSS Tax Saver Fund -Growth\nFolio 51074247016"
    )
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol=None,
            txn_type=InvestmentTxnType.SIP.value,
            quantity=30.678,
            price_per_unit=391.1375,
            total_amount=12000.0,
            account_platform="ICICI Direct MF",
            notes=email_notes,
            source_type="email",
        )
    )
    session.commit()

    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol=None,
        name="Quant Money Managers Ltd — Quant Elss Tax Saver Fund Regular Plan - Growth",
        txn_type=InvestmentTxnType.SIP.value,
        quantity=30.678,
        price_per_unit=391.1375,
        total_amount=12000.0,
        account_platform="ICICI Direct MF",
        notes="Folio 51074247016",
        metadata={"folio": "51074247016"},
    )
    assert investment_txn_exists(session, parsed) is True


def test_mf_not_duplicate_different_amount(session: Session) -> None:
    d = datetime.date(2024, 6, 9)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol=None,
            txn_type=InvestmentTxnType.SIP.value,
            quantity=30.678,
            price_per_unit=391.1375,
            total_amount=12000.0,
            account_platform="ICICI Direct MF",
            notes="Fund A\nFolio 51074247016",
            source_type="email",
        )
    )
    session.commit()

    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol=None,
        name="Fund A",
        txn_type=InvestmentTxnType.SIP.value,
        quantity=30.678,
        price_per_unit=400.0,
        total_amount=13000.0,
        account_platform="ICICI Direct MF",
        notes="Folio 51074247016",
        metadata={"folio": "51074247016"},
    )
    assert investment_txn_exists(session, parsed) is False


def test_icici_equity_blank_symbol_overlapping_pdf_vs_db_name(session: Session) -> None:
    """Two ICICI equity statement PDFs: NSE symbol unresolved — still dedupe on name + economics."""
    d = datetime.date(2020, 5, 5)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol=None,
            txn_type=InvestmentTxnType.BUY.value,
            quantity=4.0,
            price_per_unit=4610.0,
            total_amount=18440.0,
            account_platform="ICICI Direct",
            notes="BAJAJ FINSERV LTD.",
            source_type="email",
        )
    )
    session.commit()

    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol=None,
        name="BAJAJ FINSERV LTD.",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=4.0,
        price_per_unit=4610.0,
        total_amount=18440.0,
        account_platform="ICICI Direct",
        metadata={
            "isin": "INE918I01018",
            "kind": "icici_equity_transaction_statement_pdf",
        },
    )
    assert investment_txn_exists(session, parsed) is True


def test_icici_equity_blank_symbol_isin_in_db_notes(session: Session) -> None:
    """After ingest, notes include ``ISIN …`` — second PDF leg matches without relying on name drift."""
    d = datetime.date(2020, 5, 5)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol=None,
            txn_type=InvestmentTxnType.BUY.value,
            quantity=26.0,
            price_per_unit=4610.0,
            total_amount=119860.0,
            account_platform="ICICI Direct",
            notes="BAJAJ FINSERV LTD.\nISIN INE918I01018",
            source_type="email",
        )
    )
    session.commit()

    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol=None,
        name="BAJAJ FINSERV LTD.",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=26.0,
        price_per_unit=4610.0,
        total_amount=119860.0,
        account_platform="ICICI Direct",
        metadata={"isin": "INE918I01018"},
    )
    assert investment_txn_exists(session, parsed) is True


def test_ingest_skips_csv_when_email_duplicate(session: Session) -> None:
    d = datetime.date(2025, 7, 2)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol="PHOENIXLTD",
            txn_type=InvestmentTxnType.SELL.value,
            quantity=31.0,
            price_per_unit=1498.95,
            total_amount=46467.6,
            account_platform="ICICI Direct",
            source_type="email",
        )
    )
    session.commit()

    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol="PHOENIXLTD",
        txn_type=InvestmentTxnType.SELL.value,
        quantity=31.0,
        price_per_unit=1498.0,
        total_amount=46467.6,
        account_platform="ICICI Direct",
    )
    stats = ingest_investment_transactions(session, [parsed], user_id="u1", dry_run=False)
    assert stats["skipped_duplicate"] == 1
    assert stats["inserted"] == 0


def test_ingest_persists_price_source_from_metadata(session: Session) -> None:
    d = datetime.date(2022, 4, 5)
    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol="CDSL",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        price_per_unit=760.67,
        total_amount=760.67,
        account_platform="Zerodha",
        metadata={"price_source": "nse_bhav"},
    )
    stats = ingest_investment_transactions(session, [parsed], user_id="u1", dry_run=False)
    assert stats["inserted"] == 1
    row = session.exec(select(InvestmentTransaction)).first()
    assert row is not None
    assert row.price_source == "nse_bhav"


def test_ingest_defaults_price_source_to_statement(session: Session) -> None:
    d = datetime.date(2023, 1, 1)
    parsed = ParsedInvestmentTxn(
        txn_date=d,
        symbol="RELIANCE",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        price_per_unit=2500.0,
        total_amount=2500.0,
        account_platform="ICICI Direct",
    )
    ingest_investment_transactions(session, [parsed], user_id="u1", dry_run=False)
    row = session.exec(select(InvestmentTransaction)).first()
    assert row is not None
    assert row.price_source == PRICE_SOURCE_STATEMENT
