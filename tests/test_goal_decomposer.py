"""Unit tests for Sub-Plan D — goal decomposition + pattern suggestions."""

from __future__ import annotations

import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from api.models import Goal, RecurringPattern
from api.services.goal_decomposer import (
    LoanParams,
    decompose_debt_goal,
    decompose_point_in_time_goal,
    suggest_goals_from_patterns,
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


def _goal(
    *,
    name: str = "Dream home",
    target_amount: float = 200_000_000.0,
    target_date: datetime.date | None = None,
    starting_balance: float = 0.0,
    expected_return: float = 12.0,
    inflation: float | None = 6.0,
) -> Goal:
    td = target_date or datetime.date(2041, 4, 15)
    return Goal(
        id=1,
        name=name,
        goal_type="SAVINGS",
        user_id="u1",
        target_amount=target_amount,
        target_date=td,
        starting_balance=starting_balance,
        expected_return_rate=expected_return,
        goal_specific_inflation_rate=inflation,
        goal_class="POINT_IN_TIME",
        goal_subtype="HOME_PURCHASE",
    )


def test_point_in_time_pmt_20cr_15y_12pct_in_range():
    """Large goal: monthly contribution should be in the ~lakhs/month ballpark."""
    today = datetime.date(2026, 4, 15)
    g = _goal(
        target_date=datetime.date(2041, 4, 15),
    )
    res = decompose_point_in_time_goal(g, surplus=500_000.0, today=today, general_cpi=6.0)
    assert res.monthly_required > 150_000
    # Inflation + 12% return PMT over 15y lands ~₹9.6L/mo for ₹20Cr nominal target.
    assert res.monthly_required < 1_100_000
    assert res.inflation_adjusted_target is not None
    assert res.reality_check is not None
    assert res.reality_check.status in ("COMFORTABLE", "FEASIBLE", "GAP")


def test_reality_check_gap_when_surplus_too_low():
    g = _goal(target_date=datetime.date(2041, 4, 15))
    res = decompose_point_in_time_goal(
        g,
        surplus=80_000.0,
        today=datetime.date(2026, 4, 15),
    )
    assert res.reality_check is not None
    assert res.reality_check.status == "GAP"
    assert res.reality_check.gap > 0


def test_debt_split_down_payment_and_emi():
    """House 2 Cr, 20% down, 8.5% 20y → ₹40L down, EMI ~₹1.39L."""
    g = Goal(
        id=1,
        name="House",
        goal_type="SAVINGS",
        user_id="u1",
        target_amount=20_000_000.0,
        target_date=datetime.date(2030, 6, 1),
        goal_subtype="HOME_PURCHASE",
    )
    lp = LoanParams(
        total_cost=20_000_000.0,
        down_payment_pct=0.20,
        loan_interest_rate=8.5,
        loan_tenure_years=20,
    )
    res = decompose_debt_goal(g, lp)
    assert len(res.sub_goals) == 2
    down = res.sub_goals[0]
    emi_g = res.sub_goals[1]
    assert down.target_amount == pytest.approx(4_000_000.0, rel=1e-6)
    assert emi_g.recurrence_amount is not None
    assert 138_000 < emi_g.recurrence_amount < 140_000


def test_suggest_emi_and_sip_patterns(session: Session):
    """EMI-like and SIP-like patterns produce suggestions."""
    session.add(
        RecurringPattern(
            user_id="u1",
            counterparty="HDFC HOME LOAN",
            counterparty_category="Financial Services, Insurance & Banking",
            direction="OUTFLOW",
            expected_amount=50_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 1, 1),
            match_count=10,
            is_active=True,
        )
    )
    session.add(
        RecurringPattern(
            user_id="u1",
            counterparty="ICICI PRUDENTIAL MF",
            counterparty_category="Asset Markets",
            direction="OUTFLOW",
            expected_amount=25_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 1, 1),
            match_count=8,
            is_active=True,
        )
    )
    session.commit()

    sug = suggest_goals_from_patterns(session, "u1")
    kinds = {s.suggested_name for s in sug}
    assert any("EMI" in k or "premium" in k for k in kinds)
    assert any("SIP" in k or "investment" in k.lower() for k in kinds)
