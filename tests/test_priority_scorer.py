"""
Tests for Sub-Plan E — priority scoring service and /api/goals/priorities, /reorder.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import Goal
from api.services.priority_scorer import (
    PriorityBreakdown,
    asset_alignment,
    compute_priority_scores,
    consequence_severity,
    funding_feasibility,
    generate_explanation,
    time_pressure,
)
from api.services.surplus_calculator import SurplusResult

# Fixed "today" for deterministic time_pressure tests
FIXED_TODAY = datetime.date(2026, 4, 15)


def _surplus_result(uid: str = "test_user", monthly_surplus: float = 60_000.0) -> SurplusResult:
    return SurplusResult(
        user_id=uid,
        monthly_income=100_000.0,
        monthly_expense_baseline=40_000.0,
        monthly_surplus=monthly_surplus,
        surplus_path_a=monthly_surplus,
        surplus_path_b=monthly_surplus,
        months_analyzed=6,
        month_details=[],
        recurring_income_patterns=[],
        warnings=[],
    )


# ── Pure dimension tests ───────────────────────────────────────────────────


def test_time_pressure_behind_schedule_higher_when_less_time_left():
    """50% unfunded: less calendar time remaining => higher time pressure."""
    created = datetime.datetime(2025, 4, 1, tzinfo=datetime.UTC)
    g_fast = Goal(
        name="A",
        goal_type="SAVINGS",
        user_id="u",
        goal_class="POINT_IN_TIME",
        target_amount=100.0,
        starting_balance=50.0,
        target_date=datetime.date(2026, 7, 1),
        activation_status="ACTIVE",
        created_at=created,
    )
    g_slow = Goal(
        name="B",
        goal_type="SAVINGS",
        user_id="u",
        goal_class="POINT_IN_TIME",
        target_amount=100.0,
        starting_balance=50.0,
        target_date=datetime.date(2027, 4, 1),
        activation_status="ACTIVE",
        created_at=created,
    )
    ta = time_pressure(g_fast, FIXED_TODAY)
    tb = time_pressure(g_slow, FIXED_TODAY)
    assert ta > tb


def test_time_pressure_deadline_passed_is_100():
    g = Goal(
        name="Late",
        goal_type="SAVINGS",
        user_id="u",
        goal_class="POINT_IN_TIME",
        target_amount=100.0,
        starting_balance=0.0,
        target_date=datetime.date(2025, 1, 1),
        activation_status="ACTIVE",
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
    )
    assert time_pressure(g, FIXED_TODAY) == 100.0


def test_time_pressure_point_in_time_no_deadline_is_10():
    g = Goal(
        name="Grow",
        goal_type="INVESTMENT",
        user_id="u",
        goal_class="POINT_IN_TIME",
        target_amount=1_000_000.0,
        starting_balance=0.0,
        activation_status="ACTIVE",
        target_date=None,
    )
    assert time_pressure(g, FIXED_TODAY) == 10.0


def test_time_pressure_recurring_next_payment_spacing():
    """~1 month to next payment => 80; far out => 0."""
    near = Goal(
        name="EMI",
        goal_type="DEBT_PAYOFF",
        user_id="u",
        goal_class="RECURRING_CASH_FLOW",
        recurrence_amount=5000.0,
        recurrence_frequency="MONTHLY",
        recurrence_start=datetime.date(2026, 5, 10),
        activation_status="ACTIVE",
    )
    far = Goal(
        name="EMI2",
        goal_type="DEBT_PAYOFF",
        user_id="u",
        goal_class="RECURRING_CASH_FLOW",
        recurrence_amount=5000.0,
        recurrence_frequency="MONTHLY",
        recurrence_start=datetime.date(2026, 10, 1),
        activation_status="ACTIVE",
    )
    assert time_pressure(near, FIXED_TODAY) == 80.0
    assert time_pressure(far, FIXED_TODAY) == 0.0


def test_consequence_loan_payoff_gt_travel():
    loan = Goal(
        name="Loan",
        goal_type="DEBT_PAYOFF",
        user_id="u",
        goal_subtype="LOAN_PAYOFF",
        activation_status="ACTIVE",
    )
    travel = Goal(
        name="Trip",
        goal_type="SAVINGS",
        user_id="u",
        goal_subtype="TRAVEL",
        activation_status="ACTIVE",
    )
    assert consequence_severity(loan) > consequence_severity(travel)


def test_consequence_null_subtype_defaults():
    g = Goal(
        name="X",
        goal_type="SAVINGS",
        user_id="u",
        goal_subtype=None,
        activation_status="ACTIVE",
    )
    assert consequence_severity(g) == 30.0


def test_feasibility_on_track_vs_at_risk():
    """High projected / target => 30; mid ratio => 80."""
    on_track = Goal(
        name="OK",
        goal_type="SAVINGS",
        user_id="u",
        goal_class="POINT_IN_TIME",
        target_amount=100_000.0,
        starting_balance=90_000.0,
        expected_return_rate=10.0,
        target_date=datetime.date(2027, 1, 1),
        activation_status="ACTIVE",
    )
    s_on, rev_on = funding_feasibility(on_track, 50_000.0, 10_000.0, 12)
    assert s_on == 30.0
    assert rev_on is False

    # Mid ratio ~0.55 → tier 0.5–0.8 → score 80 (see projected math on mid below).
    mid = Goal(
        name="Mid",
        goal_type="SAVINGS",
        user_id="u",
        goal_class="POINT_IN_TIME",
        target_amount=100_000.0,
        starting_balance=0.0,
        expected_return_rate=0.0,
        target_date=datetime.date(2027, 1, 1),
        activation_status="ACTIVE",
    )
    s_mid, _ = funding_feasibility(mid, 50_000.0, 5_000.0, 11)
    # projected = 0 + 5000*11 = 55k, ratio 0.55 -> tier 0.5-0.8 -> 80
    assert s_mid == 80.0


def test_feasibility_impossible_flag():
    low = Goal(
        name="Bad",
        goal_type="SAVINGS",
        user_id="u",
        goal_class="POINT_IN_TIME",
        target_amount=1_000_000.0,
        starting_balance=0.0,
        expected_return_rate=0.0,
        target_date=datetime.date(2027, 1, 1),
        activation_status="ACTIVE",
    )
    s, rev = funding_feasibility(low, 50_000.0, 100.0, 6)
    # projected = 600, ratio 0.0006 < 0.2
    assert s == 60.0
    assert rev is True


def test_asset_alignment_full_coverage_scores_0(session: Session):
    g = Goal(
        name="House",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        target_amount=100_000.0,
        target_date=datetime.date(2030, 1, 1),
        activation_status="ACTIVE",
    )
    session.add(g)
    session.commit()
    session.refresh(g)

    mock_match = MagicMock()
    mock_match.total_accessible_value_inr = 100_000.0
    with patch("api.services.priority_scorer.match_holdings_to_goal", return_value=mock_match):
        score = asset_alignment(session, g, "test_user", FIXED_TODAY)
    assert score == 0.0


def test_asset_alignment_no_coverage_scores_100(session: Session):
    g = Goal(
        name="House2",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        target_amount=100_000.0,
        target_date=datetime.date(2030, 1, 1),
        activation_status="ACTIVE",
    )
    session.add(g)
    session.commit()
    session.refresh(g)

    mock_match = MagicMock()
    mock_match.total_accessible_value_inr = 0.0
    with patch("api.services.priority_scorer.match_holdings_to_goal", return_value=mock_match):
        score = asset_alignment(session, g, "test_user", FIXED_TODAY)
    assert score == 100.0


def test_explanation_contains_rank_and_name():
    g = Goal(
        name="Wedding Fund",
        goal_type="SAVINGS",
        user_id="u",
        goal_class="POINT_IN_TIME",
        activation_status="ACTIVE",
    )
    bd = PriorityBreakdown(
        time_pressure=90.0,
        consequence_severity=20.0,
        feasibility_urgency=30.0,
        asset_alignment=40.0,
    )
    expl = generate_explanation(g, 1, bd, False)
    assert "Ranked #1" in expl
    assert "Wedding Fund" in expl


# ── compute_priority_scores integration ────────────────────────────────────


@pytest.fixture(name="engine")
def in_memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(name="session")
def db_session(engine):
    with Session(engine) as session:
        yield session


@patch("api.services.priority_scorer.compute_surplus")
def test_composite_emergency_beats_travel(mock_surplus, session: Session):
    mock_surplus.return_value = _surplus_result()
    e = Goal(
        name="EF",
        goal_type="EMERGENCY_FUND",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        goal_subtype="EMERGENCY_FUND",
        target_amount=300_000.0,
        starting_balance=50_000.0,
        target_date=datetime.date(2028, 1, 1),
        activation_status="ACTIVE",
        created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    t = Goal(
        name="Trip",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        goal_subtype="TRAVEL",
        target_amount=200_000.0,
        starting_balance=10_000.0,
        target_date=datetime.date(2029, 1, 1),
        activation_status="ACTIVE",
        created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    session.add(e)
    session.add(t)
    session.commit()
    for x in (e, t):
        session.refresh(x)

    with patch("api.services.priority_scorer.match_holdings_to_goal") as mm:
        mm.return_value = MagicMock(total_accessible_value_inr=0.0)
        res = compute_priority_scores(session, "test_user", persist=False, today=FIXED_TODAY)

    ids = [p.goal_id for p in res.priorities]
    assert ids[0] == e.id
    assert res.priorities[0].suggested_rank == 1
    assert res.priorities[1].suggested_rank == 2


@patch("api.services.priority_scorer.compute_surplus")
def test_compute_persists_system_score(mock_surplus, session: Session):
    mock_surplus.return_value = _surplus_result()
    g = Goal(
        name="Only",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        target_amount=500_000.0,
        target_date=datetime.date(2035, 1, 1),
        activation_status="ACTIVE",
    )
    session.add(g)
    session.commit()
    session.refresh(g)

    with patch("api.services.priority_scorer.match_holdings_to_goal") as mm:
        mm.return_value = MagicMock(total_accessible_value_inr=0.0)
        compute_priority_scores(session, "test_user", persist=True, today=FIXED_TODAY)

    session.refresh(g)
    assert g.system_priority_score is not None


# ── HTTP API ────────────────────────────────────────────────────────────────


@pytest.fixture(name="client")
def api_client(engine):
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "test_user"

    import api.database as _db_mod

    _original_init = _db_mod.init_db
    _db_mod.init_db = lambda: None

    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _original_init
    app.dependency_overrides.clear()


@patch("api.services.priority_scorer.compute_surplus")
def test_api_get_priorities(mock_surplus, client: TestClient, session: Session):
    mock_surplus.return_value = _surplus_result()

    g = Goal(
        name="API Goal",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        target_amount=200_000.0,
        target_date=datetime.date(2030, 1, 1),
        activation_status="ACTIVE",
    )
    session.add(g)
    session.commit()

    with patch("api.services.priority_scorer.match_holdings_to_goal") as mm:
        mm.return_value = MagicMock(total_accessible_value_inr=0.0)
        r = client.get("/api/goals/priorities?persist=false")
    assert r.status_code == 200
    data = r.json()
    assert "priorities" in data
    assert 0 <= data["priorities"][0]["priority_score"] <= 100


def test_api_reorder_updates_allocation_only(client: TestClient, session: Session):

    a = Goal(
        name="A",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        target_amount=100_000.0,
        target_date=datetime.date(2030, 1, 1),
        activation_status="ACTIVE",
        system_priority_score=42.5,
    )
    b = Goal(
        name="B",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        target_amount=150_000.0,
        target_date=datetime.date(2031, 1, 1),
        activation_status="ACTIVE",
        system_priority_score=42.5,
    )
    session.add(a)
    session.add(b)
    session.commit()
    session.refresh(a)
    session.refresh(b)

    r = client.post(
        "/api/goals/reorder",
        json={
            "goal_order": [
                {"goal_id": a.id, "allocation_priority": 2},
                {"goal_id": b.id, "allocation_priority": 1},
            ]
        },
    )
    assert r.status_code == 200
    session.refresh(a)
    session.refresh(b)
    assert a.allocation_priority == 2
    assert b.allocation_priority == 1
    assert a.system_priority_score == 42.5
    assert b.system_priority_score == 42.5


def test_api_reorder_404_unknown_goal(client: TestClient):
    r = client.post(
        "/api/goals/reorder",
        json={"goal_order": [{"goal_id": 999_999, "allocation_priority": 1}]},
    )
    assert r.status_code == 404


def test_api_reorder_duplicate_ranks_400(client: TestClient, session: Session):
    g = Goal(
        name="Solo",
        goal_type="SAVINGS",
        user_id="test_user",
        goal_class="POINT_IN_TIME",
        target_amount=50_000.0,
        target_date=datetime.date(2028, 1, 1),
        activation_status="ACTIVE",
    )
    session.add(g)
    session.commit()
    session.refresh(g)

    r = client.post(
        "/api/goals/reorder",
        json={
            "goal_order": [
                {"goal_id": g.id, "allocation_priority": 1},
                {"goal_id": g.id, "allocation_priority": 1},
            ]
        },
    )
    assert r.status_code == 400
