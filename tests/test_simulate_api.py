"""
HTTP tests for ``/api/simulate`` (Sub-Plan G).
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import Goal


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


def _sim_body():
    return {
        "goals": [
            {
                "name": "Test goal",
                "goal_class": "POINT_IN_TIME",
                "allocation_priority": 1,
                "expected_return_rate": 10.0,
                "inflation_rate": 6.0,
                "starting_balance": 0.0,
                "target_amount": 50_000_000.0,
                "target_date": "2050-12-01",
            }
        ],
        "monthly_surplus": 50_000.0,
        "simulation_months": 12,
        "as_of_date": "2026-01-01",
    }


def test_post_simulate_ok(client: TestClient) -> None:
    r = client.post("/api/simulate", json=_sim_body())
    assert r.status_code == 200
    data = r.json()
    assert "projections" in data
    assert len(data["projections"]) == 1
    assert data["projections"][0]["goal_name"] == "Test goal"


def test_post_simulate_validation_error(client: TestClient) -> None:
    r = client.post(
        "/api/simulate",
        json={"goals": [], "monthly_surplus": "oops"},
    )
    assert r.status_code == 422


def test_post_compare(client: TestClient) -> None:
    b = _sim_body()
    v = {**b, "monthly_surplus": 60_000.0}
    r = client.post("/api/simulate/compare", json={"base": b, "variants": [v]})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["scenario_name"] == "variant_1"
    assert "result" in data[0]


def test_post_allocate(client: TestClient) -> None:
    r = client.post(
        "/api/simulate/allocate",
        json={
            "goals": [
                {
                    "name": "G",
                    "goal_class": "POINT_IN_TIME",
                    "allocation_priority": 1,
                    "target_amount": 1e12,
                    "target_date": "2100-01-01",
                    "starting_balance": 0.0,
                    "expected_return_rate": 10.0,
                }
            ],
            "surplus": 25_000.0,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"G": 25_000.0}


def test_post_from_current_with_seed(client: TestClient, session: Session) -> None:
    session.add(
        Goal(
            name="API sim goal",
            goal_type="SAVINGS",
            goal_class="POINT_IN_TIME",
            user_id="test_user",
            pyramid_id="SIM1",
            target_amount=500_000.0,
            target_date=datetime.date(2032, 1, 1),
            activation_status="ACTIVE",
            allocation_priority=1,
            expected_return_rate=10.0,
        )
    )
    session.commit()

    r = client.post(
        "/api/simulate/from-current",
        json={"simulation_months": 36, "surplus_trailing_months": 6},
    )
    assert r.status_code == 200
    payload = r.json()
    assert "params" in payload
    assert "meta" in payload
    assert "result" in payload
    assert payload["meta"]["user_id"] == "test_user"
    assert payload["meta"]["active_goals_loaded"] == 1
    assert len(payload["result"]["projections"]) >= 1
