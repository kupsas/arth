"""Phase A.6 — returns_calculator (XIRR path, fixed return, YTM, dispatcher)."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.models import Holding, InvestmentTransaction
from api.services import returns_calculator as rc
from pipeline.models import AssetClass, InvestmentTxnType, ValuationMethod


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


def _holding(**kwargs) -> Holding:
    defaults = dict(
        name="Test",
        asset_class=AssetClass.EQUITY.value,
        account_platform="Unit Test",
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        liquidity_class="T_PLUS_1",
        user_id="sashank",
    )
    defaults.update(kwargs)
    return Holding(**defaults)


def test_compute_xirr_two_flows_and_terminal(session: Session) -> None:
    h = _holding(
        symbol="TEST",
        quantity=10.0,
        current_price_per_unit=120.0,
        current_value=1200.0,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2024, 1, 1),
            symbol="TEST",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=10.0,
            price_per_unit=100.0,
            total_amount=1000.0,
            account_platform="Unit Test",
            holding_id=h.id,
        )
    )
    session.commit()

    x = rc.compute_xirr(h.id, session, as_of_date=datetime.date(2025, 1, 1))
    assert x is not None
    assert x > 0


def test_compute_fixed_return_implied_cagr(session: Session) -> None:
    created = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    h = _holding(
        asset_class=AssetClass.PPF.value,
        valuation_method=ValuationMethod.FIXED_RETURN.value,
        principal_amount=100_000.0,
        interest_rate=7.1,
        compounding_frequency="ANNUALLY",
        current_value=115_000.0,
        created_at=created,
        updated_at=created,
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    out = rc.compute_fixed_return(h, as_of_date=datetime.date(2022, 1, 1))
    assert out["method"] == "fixed_return"
    assert out["principal"] == pytest.approx(100_000.0)
    assert out["implied_cagr"] is not None
    assert out["implied_cagr"] > 0


def test_compute_ytm_solvable_bond(session: Session) -> None:
    h = _holding(
        asset_class=AssetClass.SOVEREIGN_GOLD_BOND.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        face_value=1000.0,
        coupon_rate=2.5,
        coupon_frequency="SEMI_ANNUAL",
        current_value=980.0,
        maturity_date=datetime.date(2030, 1, 1),
    )
    session.add(h)
    session.commit()
    session.refresh(h)

    y = rc.compute_ytm(h, as_of_date=datetime.date(2025, 6, 1))
    assert y is not None
    assert 0 < y < 0.5


def test_compute_returns_manual_branch(session: Session) -> None:
    h = _holding(valuation_method=ValuationMethod.MANUAL.value)
    session.add(h)
    session.commit()
    session.refresh(h)
    out = rc.compute_returns(h.id, session)
    assert out["method"] == "manual"


def test_compute_returns_not_found(session: Session) -> None:
    out = rc.compute_returns(99999, session)
    assert out["method"] == "unavailable"
