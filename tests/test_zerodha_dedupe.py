"""Zerodha email (source of truth) vs tradebook CSV backup dedupe."""

from __future__ import annotations

import datetime
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.models import InvestmentTransaction  # noqa: E402
from parsers.holdings.base import ParsedInvestmentTxn  # noqa: E402
from pipeline.holding_pipeline import investment_txn_exists  # noqa: E402
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


def test_csv_backup_skipped_when_email_truth_exists(session: Session) -> None:
    """Same economic line from demat email vs tradebook CSV — only one DB row."""
    d = datetime.date(2025, 6, 27)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol="RELIANCE",
            txn_type=InvestmentTxnType.SELL.value,
            quantity=5.0,
            price_per_unit=1508.5,
            total_amount=7542.5,
            account_platform="Zerodha",
            source_type="email",
            is_reviewed=False,
        )
    )
    session.commit()

    csv_row = ParsedInvestmentTxn(
        txn_date=d,
        symbol="RELIANCE",
        name="RELIANCE",
        txn_type=InvestmentTxnType.SELL.value,
        quantity=5.0,
        price_per_unit=1508.50,
        total_amount=7542.5,
        account_platform="Zerodha",
        metadata={"kind": "zerodha_tradebook_csv"},
    )
    assert investment_txn_exists(session, csv_row)


def test_csv_backup_skipped_when_email_has_zero_amount(session: Session) -> None:
    """Demat email units-only (₹0) still dedupes against tradebook with execution price."""
    d = datetime.date(2022, 4, 5)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol="CDSL",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=1.0,
            price_per_unit=0.0,
            total_amount=0.0,
            account_platform="Zerodha",
            price_source="nse_bhav",
            source_type="email",
        )
    )
    session.commit()

    csv_row = ParsedInvestmentTxn(
        txn_date=d,
        symbol="CDSL",
        name="CDSL",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        price_per_unit=760.67,
        total_amount=760.67,
        account_platform="Zerodha",
        metadata={"kind": "zerodha_tradebook_csv", "price_source": "statement"},
    )
    assert investment_txn_exists(session, csv_row)


def test_csv_backup_skipped_when_bhav_proxy_differs_from_execution(session: Session) -> None:
    """NSE bhav close vs intraday execution — within 3% still one row."""
    d = datetime.date(2022, 4, 5)
    session.add(
        InvestmentTransaction(
            txn_date=d,
            symbol="CDSL",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=10.0,
            price_per_unit=1521.35,
            total_amount=15213.5,
            account_platform="Zerodha",
            price_source="nse_bhav",
            source_type="email",
        )
    )
    session.commit()

    csv_row = ParsedInvestmentTxn(
        txn_date=d,
        symbol="CDSL",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        price_per_unit=760.67,
        total_amount=7606.7,
        account_platform="Zerodha",
        metadata={"kind": "zerodha_tradebook_csv", "price_source": "statement"},
    )
    # ~50% drift — should NOT dedupe (different economics or bad match)
    assert investment_txn_exists(session, csv_row) is False

    csv_row_close = ParsedInvestmentTxn(
        txn_date=d,
        symbol="CDSL",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        price_per_unit=1500.0,
        total_amount=15000.0,
        account_platform="Zerodha",
        metadata={"kind": "zerodha_tradebook_csv", "price_source": "statement"},
    )
    assert investment_txn_exists(session, csv_row_close) is True
