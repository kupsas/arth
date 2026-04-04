"""Tests for Sub-Plan F — inflation_service + /api/inflation routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import Goal, InflationRate
from api.services import inflation_service as inf
from api.services.inflation_service import (
    INFLATION_DEFAULTS,
    fetch_and_cache_inflation,
    get_goal_inflation_rate,
    get_inflation_rate,
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


def test_get_inflation_rate_uses_default_when_no_rows(session: Session):
    r = get_inflation_rate(session, "HEALTHCARE")
    assert r == INFLATION_DEFAULTS["HEALTHCARE"]


def test_get_goal_inflation_rate_override_and_loan_payoff(session: Session):
    g = Goal(
        name="x",
        goal_type="SAVINGS",
        user_id="u",
        goal_specific_inflation_rate=7.5,
        goal_subtype="HOME_PURCHASE",
    )
    assert get_goal_inflation_rate(session, g) == 7.5

    g2 = Goal(
        name="loan",
        goal_type="DEBT_PAYOFF",
        user_id="u",
        goal_subtype="LOAN_PAYOFF",
        goal_specific_inflation_rate=None,
    )
    assert get_goal_inflation_rate(session, g2) == 0.0


def test_fetch_and_cache_persists_mocked_cpi(session: Session):
    def fake_fetch():
        return {"CPI_GENERAL": 5.85, "_period": "2025-01"}

    with patch.object(inf, "fetch_cpi_from_data_gov_in", fake_fetch):
        merged = fetch_and_cache_inflation(session)
    assert merged["CPI_GENERAL"] == 5.85
    row = session.exec(
        select(InflationRate).where(InflationRate.category == "CPI_GENERAL")
    ).first()
    assert row is not None
    assert row.source == "MOSPI_CPI"


def test_fetch_failure_keeps_defaults(session: Session):
    with patch.object(inf, "fetch_cpi_from_data_gov_in", return_value=None):
        merged = fetch_and_cache_inflation(session)
    assert merged["CPI_GENERAL"] == INFLATION_DEFAULTS["CPI_GENERAL"]


def test_get_inflation_api_shape(client: TestClient):
    r = client.get("/api/inflation")
    assert r.status_code == 200
    body = r.json()
    assert "rates" in body
    assert "CPI_GENERAL" in body["rates"]
    assert "rate" in body["rates"]["CPI_GENERAL"]


def test_refresh_endpoint_calls_fetch(client: TestClient, engine):
    with patch.object(inf, "fetch_cpi_from_data_gov_in") as m:
        m.return_value = {"CPI_GENERAL": 6.1, "_period": "2025-06"}
        r = client.post("/api/inflation/refresh")
    assert r.status_code == 200
    assert r.json()["rates"]["CPI_GENERAL"]["rate"] == 6.1
