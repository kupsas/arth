"""Sim-on-write goal_status_cache integration (Track 3)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import api.models  # noqa: F401 — register GoalStatusCache metadata
from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import Goal, GoalStatusCache, Transaction
from api.services.goal_status_cache import simulation_fingerprint


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


@pytest.fixture(name="client")
def api_client(engine):
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "cache_user"
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_get_goals_populates_cache_and_returns_status_data(client: TestClient, engine):
    """Non–expense-limit goals get simulation-backed progress + JSON status_data."""
    r = client.post(
        "/api/goals",
        json={
            "name": "House fund",
            "goal_type": "SAVINGS",
            "goal_class": "POINT_IN_TIME",
            "target_amount": 1_000_000,
            "target_date": "2035-12-31",
            "current_value": 50_000,
            "starting_balance": 50_000,
            "priority": 2,
            "linked_layer": 3,
        },
    )
    assert r.status_code == 201, r.text
    goal_id = r.json()["id"]
    # Create response already runs compute_progress → cache may exist before list.

    lst = client.get("/api/goals")
    assert lst.status_code == 200
    body = lst.json()
    assert len(body) == 1
    g0 = body[0]
    assert g0["id"] == goal_id
    assert g0.get("status_data") is not None
    assert g0.get("projected_completion_pct") is not None or g0.get("periods_met_pct") is not None

    with Session(engine) as session:
        row = session.exec(select(GoalStatusCache).where(GoalStatusCache.goal_id == goal_id)).first()
        assert row is not None
        assert row.user_id == "cache_user"
        fp = simulation_fingerprint(session, "cache_user")
        assert row.simulation_hash == fp


def test_refresh_status_endpoint_force_rebuild(client: TestClient, engine):
    client.post(
        "/api/goals",
        json={
            "name": "Retirement",
            "goal_type": "SAVINGS",
            "goal_class": "POINT_IN_TIME",
            "target_amount": 5_000_000,
            "target_date": "2045-12-31",
            "current_value": 100_000,
            "starting_balance": 100_000,
            "priority": 2,
            "linked_layer": 3,
        },
    )
    r1 = client.post("/api/goals/refresh-status")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["refreshed_goals"] >= 1
    assert "simulation_hash" in d1

    r2 = client.post("/api/goals/refresh-status")
    assert r2.status_code == 200
    assert r2.json()["refreshed_goals"] >= 1


def test_delete_goal_removes_cache_row_no_fk_error(client: TestClient, engine):
    create = client.post(
        "/api/goals",
        json={
            "name": "Temp",
            "goal_type": "SAVINGS",
            "goal_class": "POINT_IN_TIME",
            "target_amount": 100_000,
            "target_date": "2030-06-01",
            "current_value": 0,
            "starting_balance": 0,
            "priority": 3,
            "linked_layer": 3,
        },
    )
    gid = create.json()["id"]
    client.get("/api/goals")

    with Session(engine) as session:
        assert session.exec(select(GoalStatusCache).where(GoalStatusCache.goal_id == gid)).first()

    del_r = client.delete(f"/api/goals/{gid}")
    assert del_r.status_code == 204

    with Session(engine) as session:
        assert session.exec(select(GoalStatusCache).where(GoalStatusCache.goal_id == gid)).first() is None
        assert session.get(Goal, gid) is None


def test_expense_limit_skips_sim_cache(client: TestClient, engine):
    """EXPENSE_LIMIT stays on transaction math; status_data stays null without txns."""
    r = client.post(
        "/api/goals",
        json={
            "name": "Food cap",
            "goal_type": "EXPENSE_LIMIT",
            "target_amount": 10_000,
            "priority": 3,
            "linked_layer": 3,
        },
    )
    assert r.status_code == 201
    gid = r.json()["id"]

    lst = client.get("/api/goals")
    g0 = next(x for x in lst.json() if x["id"] == gid)
    assert g0.get("status_data") is None

    with Session(engine) as session:
        assert session.exec(select(GoalStatusCache).where(GoalStatusCache.goal_id == gid)).first() is None


def test_fingerprint_changes_when_transaction_inserted(client: TestClient, engine):
    """New bank rows for the user invalidate the hash (surplus inputs change)."""
    fp1 = simulation_fingerprint(Session(engine), "cache_user")
    with Session(engine) as s:
        s.add(
            Transaction(
                content_hash=uuid.uuid4().hex,
                txn_date=datetime.date(2024, 1, 15),
                account_id="acct_test",
                user_id="cache_user",
                source_statement="test",
                direction="OUTFLOW",
                amount=100.0,
                raw_description="test row",
            )
        )
        s.commit()
    fp2 = simulation_fingerprint(Session(engine), "cache_user")
    assert fp1 != fp2
