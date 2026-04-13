"""Statutory PPF maturity (India) — FY boundary + 15 years."""

from __future__ import annotations

import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from api.models import Holding, InvestmentTransaction
from api.services.ppf_maturity import (
    computed_ppf_maturity_date,
    earliest_ppf_contribution_date,
    effective_ppf_maturity_date,
)
from pipeline.holding_parsers.icici_ppf import parse_icici_ppf_csv
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod
from pipeline.ppf_maturity import indian_fy_end_containing


@pytest.mark.parametrize(
    ("d", "want_end"),
    [
        (datetime.date(2010, 8, 15), datetime.date(2011, 3, 31)),
        (datetime.date(2020, 4, 5), datetime.date(2021, 3, 31)),
        (datetime.date(2020, 3, 10), datetime.date(2020, 3, 31)),
        (datetime.date(2020, 3, 31), datetime.date(2020, 3, 31)),
    ],
)
def test_indian_fy_end_containing(d: datetime.date, want_end: datetime.date) -> None:
    assert indian_fy_end_containing(d) == want_end


def test_ppf_statutory_maturity_from_fixture_csv() -> None:
    # First BUY in icici_ppf_min.csv is 05-Apr-2020 → FY end 31-Mar-2021 → +15y = 31-Mar-2036
    from pathlib import Path

    path = Path(__file__).resolve().parent / "fixtures" / "holdings" / "icici_ppf_min.csv"
    holdings, _txns = parse_icici_ppf_csv(path)
    assert holdings[0].maturity_date == datetime.date(2036, 3, 31)


def test_computed_ppf_maturity_from_db() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        h = Holding(
            name="PPF",
            asset_class=AssetClass.PPF.value,
            account_platform="ICICI PPF",
            valuation_method=ValuationMethod.FIXED_RETURN.value,
            liquidity_class=LiquidityClass.ILLIQUID.value,
            user_id="u1",
        )
        session.add(h)
        session.commit()
        session.refresh(h)
        assert h.id is not None
        session.add(
            InvestmentTransaction(
                txn_date=datetime.date(2020, 4, 5),
                txn_type=InvestmentTxnType.BUY.value,
                quantity=1,
                price_per_unit=1,
                total_amount=1,
                account_platform="ICICI PPF",
                holding_id=h.id,
            )
        )
        session.commit()
        assert earliest_ppf_contribution_date(session, h.id) == datetime.date(2020, 4, 5)
        assert computed_ppf_maturity_date(session, h.id) == datetime.date(2036, 3, 31)
        assert effective_ppf_maturity_date(
            session, holding_id=h.id, stored_maturity=None, asset_class=AssetClass.PPF.value
        ) == datetime.date(2036, 3, 31)
        # Statutory from ledger overrides wrong/stale stored maturity
        assert effective_ppf_maturity_date(
            session,
            holding_id=h.id,
            stored_maturity=datetime.date(2040, 1, 1),
            asset_class=AssetClass.PPF.value,
        ) == datetime.date(2036, 3, 31)


def test_effective_ppf_maturity_falls_back_to_stored_when_no_ledger_buy() -> None:
    """No BUY rows → cannot compute statutory date; keep whatever is on the holding row."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        h = Holding(
            name="PPF",
            asset_class=AssetClass.PPF.value,
            account_platform="ICICI PPF",
            valuation_method=ValuationMethod.FIXED_RETURN.value,
            liquidity_class=LiquidityClass.ILLIQUID.value,
            user_id="u1",
        )
        session.add(h)
        session.commit()
        session.refresh(h)
        assert h.id is not None
        want = datetime.date(2031, 3, 31)
        assert (
            effective_ppf_maturity_date(
                session,
                holding_id=h.id,
                stored_maturity=want,
                asset_class=AssetClass.PPF.value,
            )
            == want
        )
