from __future__ import annotations

import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.models import Holding, HoldingValueSnapshot, InvestmentTransaction, Price
from api.services.historical_portfolio import historical_price_symbol_universe, price_coverage_report
from api.services.net_worth import compute_net_worth_history
from pipeline.holding_parsers.base import ParsedHolding
from pipeline.holding_pipeline import ingest_holdings
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


def _holding(
    *,
    name: str,
    symbol: str | None,
    asset_class: str,
    valuation_method: str,
    account_platform: str,
    user_id: str = "sashank",
    is_active: bool = True,
    quantity: float | None = None,
    current_value: float | None = None,
    created_at: datetime.datetime | None = None,
) -> Holding:
    return Holding(
        name=name,
        symbol=symbol,
        quantity=quantity,
        asset_class=asset_class,
        account_platform=account_platform,
        valuation_method=valuation_method,
        liquidity_class=LiquidityClass.T_PLUS_1.value,
        current_value=current_value,
        user_id=user_id,
        is_active=is_active,
        created_at=created_at or datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        updated_at=created_at or datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
    )


def _add_price(session: Session, symbol: str, date_s: str, close_price: float) -> None:
    session.add(
        Price(
            symbol=symbol,
            date=datetime.date.fromisoformat(date_s),
            close_price=close_price,
            source="test",
        )
    )


def _add_txn(
    session: Session,
    *,
    holding_id: int,
    symbol: str | None,
    txn_date: str,
    txn_type: str,
    quantity: float,
    total_amount: float,
    platform: str,
) -> None:
    session.add(
        InvestmentTransaction(
            txn_date=datetime.date.fromisoformat(txn_date),
            symbol=symbol,
            txn_type=txn_type,
            quantity=quantity,
            price_per_unit=(total_amount / quantity) if quantity else total_amount,
            total_amount=total_amount,
            account_platform=platform,
            holding_id=holding_id,
        )
    )


def test_compute_net_worth_history_replays_positions_and_balances(session: Session) -> None:
    infy = _holding(
        name="Infosys",
        symbol="INFY",
        asset_class=AssetClass.EQUITY.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        account_platform="ICICI Direct",
        quantity=999.0,
        current_value=999_999.0,
    )
    bajaj = _holding(
        name="Bajaj Finance",
        symbol="BAJFINANCE",
        asset_class=AssetClass.EQUITY.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        account_platform="ICICI Direct",
        is_active=False,
    )
    ppf = _holding(
        name="Public Provident Fund (PPF)",
        symbol=None,
        asset_class=AssetClass.PPF.value,
        valuation_method=ValuationMethod.FIXED_RETURN.value,
        account_platform="ICICI PPF",
        current_value=999_999.0,
    )
    nps = _holding(
        name="National Pension System (NPS)",
        symbol=None,
        asset_class=AssetClass.NPS.value,
        valuation_method=ValuationMethod.MANUAL.value,
        account_platform="NPS (CRA)",
        current_value=999_999.0,
    )
    session.add(infy)
    session.add(bajaj)
    session.add(ppf)
    session.add(nps)
    session.commit()
    session.refresh(infy)
    session.refresh(bajaj)
    session.refresh(ppf)
    session.refresh(nps)

    _add_txn(
        session,
        holding_id=infy.id,
        symbol="INFY",
        txn_date="2024-04-01",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        total_amount=1000.0,
        platform="ICICI Direct",
    )
    _add_txn(
        session,
        holding_id=bajaj.id,
        symbol="BAJFINANCE",
        txn_date="2024-04-01",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=2.0,
        total_amount=400.0,
        platform="ICICI Direct",
    )
    _add_txn(
        session,
        holding_id=bajaj.id,
        symbol="BAJFINANCE",
        txn_date="2024-06-15",
        txn_type=InvestmentTxnType.SELL.value,
        quantity=2.0,
        total_amount=460.0,
        platform="ICICI Direct",
    )
    _add_txn(
        session,
        holding_id=ppf.id,
        symbol=None,
        txn_date="2024-04-20",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        total_amount=500.0,
        platform="ICICI PPF",
    )
    _add_txn(
        session,
        holding_id=ppf.id,
        symbol=None,
        txn_date="2024-05-01",
        txn_type=InvestmentTxnType.DIVIDEND.value,
        quantity=1.0,
        total_amount=50.0,
        platform="ICICI PPF",
    )
    _add_txn(
        session,
        holding_id=ppf.id,
        symbol=None,
        txn_date="2024-06-20",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=1.0,
        total_amount=1000.0,
        platform="ICICI PPF",
    )
    session.add(
        HoldingValueSnapshot(
            holding_id=nps.id,
            snapshot_date=datetime.date(2024, 4, 1),
            value=1000.0,
            source="statement",
        )
    )
    session.add(
        HoldingValueSnapshot(
            holding_id=nps.id,
            snapshot_date=datetime.date(2024, 6, 1),
            value=1400.0,
            source="statement",
        )
    )
    _add_price(session, "INFY", "2024-04-01", 100.0)
    _add_price(session, "INFY", "2024-05-01", 110.0)
    _add_price(session, "INFY", "2024-06-01", 120.0)
    _add_price(session, "INFY", "2024-06-30", 130.0)
    _add_price(session, "BAJFINANCE", "2024-04-01", 200.0)
    _add_price(session, "BAJFINANCE", "2024-05-01", 210.0)
    _add_price(session, "BAJFINANCE", "2024-06-01", 220.0)
    _add_price(session, "BAJFINANCE", "2024-06-30", 230.0)
    session.commit()

    series = compute_net_worth_history(
        session,
        datetime.date(2024, 4, 1),
        datetime.date(2024, 6, 30),
        granularity="monthly",
        user_id="sashank",
    )

    # Monthly anchors are month-end (or `end` for the last month); not month-start.
    assert [point["date"] for point in series] == [
        "2024-04-30",
        "2024-05-31",
        "2024-06-30",
    ]
    assert [point["total_assets"] for point in series] == [2900.0, 3070.0, 4250.0]


def test_historical_price_universe_uses_old_txns_and_excludes_stoone(session: Session) -> None:
    active = _holding(
        name="Infosys",
        symbol="INFY",
        asset_class=AssetClass.EQUITY.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        account_platform="ICICI Direct",
    )
    sold = _holding(
        name="Old MF",
        symbol="119551",
        asset_class=AssetClass.MUTUAL_FUND.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        account_platform="ICICI Direct MF",
        is_active=False,
    )
    stoone = _holding(
        name="Stone India",
        symbol="STOONE",
        asset_class=AssetClass.EQUITY.value,
        valuation_method=ValuationMethod.MARKET_PRICE.value,
        account_platform="ICICI Direct",
    )
    session.add(active)
    session.add(sold)
    session.add(stoone)
    session.commit()
    session.refresh(active)
    session.refresh(sold)
    session.refresh(stoone)

    _add_txn(
        session,
        holding_id=sold.id,
        symbol="119551",
        txn_date="2024-01-01",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=10.0,
        total_amount=1000.0,
        platform="ICICI Direct MF",
    )
    _add_txn(
        session,
        holding_id=stoone.id,
        symbol="STOONE",
        txn_date="2024-01-01",
        txn_type=InvestmentTxnType.BUY.value,
        quantity=5.0,
        total_amount=500.0,
        platform="ICICI Direct",
    )
    _add_price(session, "INFY", "2024-01-01", 10.0)
    _add_price(session, "119551", "2024-01-01", 20.0)
    session.commit()

    universe = historical_price_symbol_universe(session, user_id="sashank")
    assert universe["nse_symbols"] == ["INFY"]
    assert universe["mf_codes"] == ["119551"]
    assert "STOONE" not in universe["nse_symbols"]

    report = price_coverage_report(
        session,
        user_id="sashank",
        start_date=datetime.date(2024, 1, 1),
        end_date=datetime.date(2024, 12, 31),
    )
    assert {(row.symbol, row.symbol_kind) for row in report} == {("INFY", "nse"), ("119551", "mf")}


def test_historical_price_universe_includes_orphan_non_icici_platform(session: Session) -> None:
    session.add(
        _holding(
            name="Anchor",
            symbol="INFY",
            asset_class=AssetClass.EQUITY.value,
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            account_platform="Zerodha",
        )
    )
    session.commit()
    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2020, 5, 5),
            symbol="WIPRO",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=2.0,
            price_per_unit=500.0,
            total_amount=1000.0,
            account_platform="Zerodha",
            holding_id=None,
            notes="Wipro",
        )
    )
    session.commit()

    universe = historical_price_symbol_universe(session, user_id="sashank")
    assert "WIPRO" in universe["nse_symbols"]


def test_historical_price_universe_includes_orphan_equity_transactions(session: Session) -> None:
    session.add(
        _holding(
            name="Infosys",
            symbol="INFY",
            asset_class=AssetClass.EQUITY.value,
            valuation_method=ValuationMethod.MARKET_PRICE.value,
            account_platform="ICICI Direct",
        )
    )
    session.commit()
    session.add(
        InvestmentTransaction(
            txn_date=datetime.date(2020, 5, 5),
            symbol="CIPLA",
            txn_type=InvestmentTxnType.BUY.value,
            quantity=2.0,
            price_per_unit=500.0,
            total_amount=1000.0,
            account_platform="ICICI Direct",
            holding_id=None,
            notes="CIPLA",
        )
    )
    session.commit()

    universe = historical_price_symbol_universe(session, user_id="sashank")
    assert "CIPLA" in universe["nse_symbols"]


def test_nps_ingest_persists_statement_snapshots(session: Session) -> None:
    stats = ingest_holdings(
        session,
        [
            ParsedHolding(
                name="National Pension System (NPS)",
                symbol=None,
                quantity=None,
                asset_class=AssetClass.NPS.value,
                valuation_method=ValuationMethod.MANUAL.value,
                account_platform="NPS (CRA)",
                current_value=1200.0,
                liquidity_class=LiquidityClass.ILLIQUID.value,
                folio_number="123456789012",
                metadata={
                    "pran": "123456789012",
                    "source_file": "nps_2024.csv",
                    "value_as_of_date": "2024-04-01",
                    "snapshot_value": 1200.0,
                },
            ),
            ParsedHolding(
                name="National Pension System (NPS)",
                symbol=None,
                quantity=None,
                asset_class=AssetClass.NPS.value,
                valuation_method=ValuationMethod.MANUAL.value,
                account_platform="NPS (CRA)",
                current_value=1500.0,
                liquidity_class=LiquidityClass.ILLIQUID.value,
                folio_number="123456789012",
                metadata={
                    "pran": "123456789012",
                    "source_file": "nps_2025.csv",
                    "value_as_of_date": "2025-04-01",
                    "snapshot_value": 1500.0,
                },
            ),
        ],
        user_id="sashank",
    )
    assert stats["inserted"] == 1
    assert stats["updated"] == 1

    holdings = list(session.exec(select(Holding)).all())
    assert len(holdings) == 1
    snaps = list(session.exec(select(HoldingValueSnapshot).order_by(HoldingValueSnapshot.snapshot_date)).all())
    assert [(s.snapshot_date.isoformat(), s.value) for s in snaps] == [
        ("2024-04-01", 1200.0),
        ("2025-04-01", 1500.0),
    ]
