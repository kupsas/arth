"""HTTP tests for POST /api/goals/{id}/decompose and GET /api/goal-suggestions."""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import Goal, RecurringPattern


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


@pytest.fixture(name="client")
def _client(engine):
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "test_user"
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_decompose_preview_returns_json(client: TestClient, engine):
    with Session(engine) as session:
        g = Goal(
            name="Big goal",
            goal_type="SAVINGS",
            user_id="test_user",
            target_amount=50_000_000.0,
            target_date=datetime.date(2040, 1, 1),
            expected_return_rate=12.0,
            goal_class="POINT_IN_TIME",
            starting_balance=0.0,
        )
        session.add(g)
        session.commit()
        session.refresh(g)
        gid = g.id

    r = client.post(f"/api/goals/{gid}/decompose", json={"auto_create": False})
    assert r.status_code == 200
    data = r.json()
    assert "decomposition" in data
    assert data["decomposition"]["mode"] == "POINT_IN_TIME"
    assert data["decomposition"]["monthly_required"] > 0
    sim = data.get("simulation_inflation")
    assert sim is not None
    assert sim["method"] == "cpi_general_ema"
    assert sim["ema_span"] >= 1
    assert "annual_pct" in sim


def test_decompose_auto_create_sets_parent_goal_id(client: TestClient, engine):
    with Session(engine) as session:
        g = Goal(
            name="Parent",
            goal_type="SAVINGS",
            user_id="test_user",
            target_amount=10_000_000.0,
            target_date=datetime.date(2035, 6, 1),
            expected_return_rate=10.0,
            goal_class="POINT_IN_TIME",
        )
        session.add(g)
        session.commit()
        session.refresh(g)
        gid = g.id

    r = client.post(
        f"/api/goals/{gid}/decompose",
        json={"auto_create": True, "surplus_months": 6},
    )
    assert r.status_code == 200
    created = r.json().get("created_goal_ids") or []
    assert len(created) >= 1

    with Session(engine) as session:
        q = select(Goal).where(Goal.parent_goal_id == gid)
        rows = list(session.exec(q).all())
        assert len(rows) >= 1


def test_decompose_duplicate_children_rejected(client: TestClient, engine):
    with Session(engine) as session:
        g = Goal(
            name="P",
            goal_type="SAVINGS",
            user_id="test_user",
            target_amount=5_000_000.0,
            target_date=datetime.date(2032, 1, 1),
            expected_return_rate=10.0,
            goal_class="POINT_IN_TIME",
        )
        c = Goal(
            name="Child",
            goal_type="SAVINGS",
            user_id="test_user",
            target_amount=1.0,
            target_date=datetime.date(2032, 1, 1),
        )
        session.add(g)
        session.add(c)
        session.commit()
        session.refresh(g)
        session.refresh(c)
        c.parent_goal_id = g.id
        session.add(c)
        session.commit()
        gid = g.id

    r = client.post(f"/api/goals/{gid}/decompose", json={})
    assert r.status_code == 400


def test_goal_suggestions_endpoint(client: TestClient, engine):
    with Session(engine) as session:
        session.add(
            RecurringPattern(
                user_id="test_user",
                counterparty="LOAN EMI",
                counterparty_category="Financial Services, Insurance & Banking",
                direction="OUTFLOW",
                expected_amount=40_000.0,
                frequency="MONTHLY",
                last_seen_date=datetime.date(2026, 1, 1),
                match_count=8,
                is_active=True,
            )
        )
        session.commit()

    r = client.get("/api/goal-suggestions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1
