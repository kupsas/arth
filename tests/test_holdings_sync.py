"""Holdings sync from investment_transactions + orphan auto-create."""

from __future__ import annotations

import datetime
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.models import Holding, InvestmentTransaction  # noqa: E402
from api.services.holdings_sync import (  # noqa: E402
    ensure_holding_for_transaction,
    sync_holding_from_transactions,
)
from pipeline.holding_parsers.nps import NPS_CANONICAL_HOLDING_NAME  # noqa: E402
from pipeline.models import (  # noqa: E402
    AssetClass,
    InvestmentTxnType,
    LiquidityClass,
    ValuationMethod,
)


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


def _equity_holding(**kwargs) -> Holding:
    defaults = dict(
        name="Test Equity",
        symbol="RELIANCE",
        asset_class=AssetClass.EQUITY.value,
        account_platform="ICICI Direct",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_1.value,
        user_id="u1",
        current_price_per_unit=2500.0,
        is_active=True,
    )
    defaults.update(kwargs)
    return Holding(**defaults)


def test_sync_buy_increases_quantity_and_avg_cost(session: Session) -> None:
    h = _equity_holding()
    session.add(h)
    session.commit()
    session.refresh(h)

    t = InvestmentTransaction(
        txn_date=datetime.date(2024, 1, 10),
        symbol="RELIANCE",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        price_per_unit=100.0,
        total_amount=1000.0,
        account_platform="ICICI Direct",
        holding_id=h.id,
    )
    session.add(t)
    session.commit()

    out = sync_holding_from_transactions(session, h.id)
    session.commit()
    session.refresh(h)

    assert out["status"] == "ok"
    assert h.quantity == 10.0
    assert h.average_cost_per_unit == 100.0
    assert h.is_active is True
    assert h.current_value == 10.0 * 2500.0


def test_sync_sell_reduces_quantity_avg_unchanged_for_remainder(session: Session) -> None:
    h = _equity_holding(current_price_per_unit=200.0)
    session.add(h)
    session.commit()
    session.refresh(h)

    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2024, 1, 1),
            symbol="RELIANCE",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=10.0,
            price_per_unit=100.0,
            total_amount=1000.0,
            account_platform="ICICI Direct",
            holding_id=h.id,
        )
    )
    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2024, 2, 1),
            symbol="RELIANCE",
            txn_type=InvestmentTxnType.SELL.value,
            quantity=4.0,
            price_per_unit=110.0,
            total_amount=440.0,
            account_platform="ICICI Direct",
            holding_id=h.id,
        )
    )
    session.commit()

    sync_holding_from_transactions(session, h.id)
    session.commit()
    session.refresh(h)

    assert h.quantity == 6.0
    assert h.average_cost_per_unit == 100.0
    assert h.is_active is True


def test_sync_full_sell_deactivates(session: Session) -> None:
    h = _equity_holding(current_price_per_unit=100.0)
    session.add(h)
    session.commit()
    session.refresh(h)

    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2024, 1, 1),
            symbol="RELIANCE",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=10.0,
            price_per_unit=100.0,
            total_amount=1000.0,
            account_platform="ICICI Direct",
            holding_id=h.id,
        )
    )
    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2024, 2, 1),
            symbol="RELIANCE",
            txn_type=InvestmentTxnType.SELL.value,
            quantity=10.0,
            price_per_unit=120.0,
            total_amount=1200.0,
            account_platform="ICICI Direct",
            holding_id=h.id,
        )
    )
    session.commit()

    sync_holding_from_transactions(session, h.id)
    session.commit()
    session.refresh(h)

    assert h.quantity == 0.0
    assert h.is_active is False
    assert h.current_value == 0.0


def test_sync_rebuy_reactivates(session: Session) -> None:
    h = _equity_holding(current_price_per_unit=100.0)
    session.add(h)
    session.commit()
    session.refresh(h)

    for txn_date, tt, q, amt in [
        (datetime.date(2024, 1, 1), InvestmentTxnType.BUY.value, 10.0, 1000.0),
        (datetime.date(2024, 2, 1), InvestmentTxnType.SELL.value, 10.0, 1000.0),
        (datetime.date(2026, 3, 1), InvestmentTxnType.BUY.value, 5.0, 750.0),
    ]:
        session.add(
            InvestmentTransaction(
                txn_date=txn_date,
                symbol="RELIANCE",
                txn_type=tt,
                quantity=q,
                price_per_unit=amt / q if q else 0,
                total_amount=amt,
                account_platform="ICICI Direct",
                holding_id=h.id,
            )
        )
    session.commit()

    sync_holding_from_transactions(session, h.id)
    session.commit()
    session.refresh(h)

    assert h.quantity == 5.0
    assert h.is_active is True
    assert h.average_cost_per_unit == 150.0


def test_sync_ppf_running_balance(session: Session) -> None:
    h = Holding(
        name="PPF",
        asset_class=AssetClass.PPF.value,
        account_platform="ICICI PPF",
        valuation_method=ValuationMethod.FIXED_RETURN.value,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        user_id="u1",
        current_value=0.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2024, 4, 1),
            symbol=None,
            txn_type=InvestmentTxnType.BUY.value,
            quantity=1.0,
            price_per_unit=5000.0,
            total_amount=5000.0,
            account_platform="ICICI PPF",
            holding_id=h.id,
        )
    )
    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2024, 4, 2),
            symbol=None,
            txn_type=InvestmentTxnType.DIVIDEND.value,
            quantity=1.0,
            price_per_unit=100.0,
            total_amount=100.0,
            account_platform="ICICI PPF",
            holding_id=h.id,
        )
    )
    session.commit()

    sync_holding_from_transactions(session, h.id)
    session.commit()
    session.refresh(h)

    assert h.current_value == 5100.0
    assert h.is_active is True


def test_sync_nps_skipped(session: Session) -> None:
    h = Holding(
        name=NPS_CANONICAL_HOLDING_NAME,
        asset_class=AssetClass.NPS.value,
        account_platform="NPS (CRA)",
        valuation_method=ValuationMethod.MANUAL.value,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        user_id="u1",
        current_value=99_999.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    out = sync_holding_from_transactions(session, h.id)
    assert out["status"] == "skipped"
    session.refresh(h)
    assert h.current_value == 99_999.0


def test_ensure_holding_creates_icici_equity(session: Session) -> None:
    txn = InvestmentTransaction(
        txn_date=datetime.date(2024, 6, 1),
        symbol="INFY",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        price_per_unit=1500.0,
        total_amount=1500.0,
        account_platform="ICICI Direct",
        holding_id=None,
        notes="Infosys Ltd",
    )
    session.add(txn)
    session.flush()

    hid = ensure_holding_for_transaction(session, txn, user_id="u1")
    session.commit()
    assert hid is not None
    session.refresh(txn)
    assert txn.holding_id == hid

    h = session.get(Holding, hid)
    assert h is not None
    assert h.symbol == "INFY"
    assert h.asset_class == AssetClass.EQUITY.value


def test_ensure_holding_non_buy_skipped(session: Session) -> None:
    txn = InvestmentTransaction(
        txn_date=datetime.date(2024, 6, 1),
        symbol="INFY",
        txn_type=InvestmentTxnType.SELL.value,
        quantity=1.0,
        price_per_unit=1500.0,
        total_amount=1500.0,
        account_platform="ICICI Direct",
        holding_id=None,
    )
    session.add(txn)
    session.flush()
    assert ensure_holding_for_transaction(session, txn, user_id="u1") is None


def test_find_stored_txn_links_inactive_mf_holding(session: Session) -> None:
    """Regression: MF matching must include inactive (fully redeemed) holdings."""
    from pipeline.investment_txn_linking import find_holding_id_for_stored_txn

    h = Holding(
        name="Old Fund (103024)",
        symbol="103024",
        asset_class=AssetClass.MUTUAL_FUND.value,
        account_platform="ICICI Direct MF",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class=LiquidityClass.T_PLUS_3.value,
        user_id="u1",
        quantity=0.0,
        is_active=False,
        current_value=0.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    orphan = InvestmentTransaction(
        txn_date=datetime.date(2023, 1, 1),
        symbol="103024",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        price_per_unit=100.0,
        total_amount=100.0,
        account_platform="ICICI Direct MF",
        holding_id=None,
    )
    session.add(orphan)
    session.commit()

    assert find_holding_id_for_stored_txn(session, "u1", orphan) == h.id
