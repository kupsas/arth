"""Phase A.6 — net_worth service (totals, allocation, history)."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.models import Holding, InvestmentTransaction, Liability, Price
from api.services import net_worth as nw
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod


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


def test_compute_net_worth_assets_minus_liabilities(session: Session) -> None:
    session.add(
        Holding(
            name="EQ",
            asset_class=AssetClass.EQUITY.value,
            account_platform="X",
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=500_000.0,
            user_id="sashank",
        )
    )
    session.add(
        Liability(
            name="Loan",
            liability_type="SECURED_LOAN",
            principal_outstanding=100_000.0,
            interest_rate=8.5,
            user_id="sashank",
        )
    )
    session.commit()

    out = nw.compute_net_worth(session, user_id="sashank")
    assert out["total_assets"] == pytest.approx(500_000.0)
    assert out["total_liabilities"] == pytest.approx(100_000.0)
    assert out["net_worth"] == pytest.approx(400_000.0)
    # No last_valued_date on the holding → live snapshot has no price anchor yet
    assert out["as_of"] is None


def test_compute_net_worth_live_as_of_uses_latest_mark_valuation_date(session: Session) -> None:
    d = datetime.date(2026, 3, 27)
    session.add(
        Holding(
            name="EQ",
            asset_class=AssetClass.EQUITY.value,
            account_platform="X",
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=100_000.0,
            user_id="sashank",
            last_valued_date=d,
        )
    )
    session.commit()
    out = nw.compute_net_worth(session, user_id="sashank")
    assert out["as_of"] == d.isoformat()


def test_compute_asset_allocation_percentages(session: Session) -> None:
    session.add(
        Holding(
            name="A",
            asset_class=AssetClass.EQUITY.value,
            account_platform="P1",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=75_000.0,
            user_id="sashank",
        )
    )
    session.add(
        Holding(
            name="B",
            asset_class=AssetClass.MUTUAL_FUND.value,
            account_platform="P2",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.T_PLUS_3.value,
            current_value=25_000.0,
            user_id="sashank",
        )
    )
    session.commit()

    alloc = nw.compute_asset_allocation(session, user_id="sashank")
    assert alloc["by_asset_class"]["EQUITY"] == pytest.approx(75.0)
    assert alloc["by_asset_class"]["MUTUAL_FUND"] == pytest.approx(25.0)


def test_compute_concentration_largest_and_esop(session: Session) -> None:
    session.add(
        Holding(
            name="Big",
            asset_class=AssetClass.EQUITY.value,
            account_platform="X",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=90_000.0,
            user_id="sashank",
        )
    )
    session.add(
        Holding(
            name="ESOP Co",
            asset_class=AssetClass.ESOP.value,
            account_platform="Y",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.ILLIQUID.value,
            current_value=10_000.0,
            user_id="sashank",
        )
    )
    session.commit()

    c = nw.compute_concentration(session, user_id="sashank")
    assert c["largest_holding_pct"] == pytest.approx(90.0)
    assert c["largest_holding_name"] == "Big"
    assert c["esop_pct"] == pytest.approx(10.0)


def test_historical_mark_uses_price_table(session: Session) -> None:
    session.add(
        Holding(
            name="Listed",
            symbol="TESTNSE",
            quantity=10.0,
            asset_class=AssetClass.EQUITY.value,
            account_platform="Z",
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            current_value=999.0,
            user_id="sashank",
            # Historical replay rejects marks before the holding existed.
            created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        )
    )
    session.add(
        Price(symbol="TESTNSE", date=datetime.date(2025, 1, 10), close_price=100.0, source="nse")
    )
    session.commit()

    h = session.exec(select(Holding)).first()
    assert h is not None
    # Market-replay equity needs at least one BUY in the ledger; quantity × price as-of.
    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2025, 1, 5),
            symbol="TESTNSE",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=10.0,
            price_per_unit=100.0,
            total_amount=1000.0,
            account_platform="Z",
            holding_id=h.id,
        )
    )
    session.commit()

    v = nw._holding_value(session, h, datetime.date(2025, 1, 15))
    assert v == pytest.approx(1000.0)


def test_compute_net_worth_history_monthly(session: Session) -> None:
    session.add(
        Holding(
            name="Cash-like",
            asset_class=AssetClass.SAVINGS.value,
            account_platform="B",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.INSTANT.value,
            current_value=50_000.0,
            user_id="sashank",
        )
    )
    session.commit()

    pts = nw.compute_net_worth_history(
        session,
        datetime.date(2025, 1, 1),
        datetime.date(2025, 3, 1),
        "monthly",
        user_id="sashank",
    )
    assert len(pts) >= 2
    assert all("net_worth" in p for p in pts)
