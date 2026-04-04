"""Tests for Sub-Plan F — inflation_service + /api/inflation routes."""

from __future__ import annotations

import datetime
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
    _yoy_ema_last,
    cpi_general_yoy_ema_pct,
    fetch_and_cache_inflation,
    get_goal_inflation_rate,
    get_inflation_rate,
    simulation_inflation_ema_span,
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


def test_yoy_ema_last_matches_pandas_style_span_3() -> None:
    """α=2/4=0.5: 2→3→4.5 over [2,4,6]."""
    assert round(_yoy_ema_last([2.0, 4.0, 6.0], 3), 2) == 4.5


def test_cpi_ema_from_db(session: Session, monkeypatch: pytest.MonkeyPatch):
    """DB rows feed chronological EMA (span controls α, not only row count)."""
    monkeypatch.setenv("INFLATION_SIMULATION_EMA_SPAN", "3")
    assert simulation_inflation_ema_span() == 3
    now = datetime.datetime.now(datetime.UTC)
    for period, rate in [("2025-10", 2.0), ("2025-11", 4.0), ("2025-12", 6.0)]:
        session.add(
            InflationRate(
                category="CPI_GENERAL",
                rate=rate,
                source="IMF_SDMX",
                period=period,
                user_id="system",
                fetched_at=now,
            )
        )
    session.commit()
    assert cpi_general_yoy_ema_pct(session) == 4.5


def test_get_goal_inflation_rate_uses_ema(session: Session, monkeypatch):
    monkeypatch.setenv("INFLATION_SIMULATION_EMA_SPAN", "2")
    now = datetime.datetime.now(datetime.UTC)
    for period, rate in [("2025-11", 10.0), ("2025-12", 20.0)]:
        session.add(
            InflationRate(
                category="CPI_GENERAL",
                rate=rate,
                source="IMF_SDMX",
                period=period,
                user_id="system",
                fetched_at=now,
            )
        )
    session.commit()
    g = Goal(
        name="edu",
        goal_type="SAVINGS",
        user_id="u",
        goal_subtype="CHILD_EDUCATION",
        goal_specific_inflation_rate=None,
    )
    # α=2/3: 10 → 20*(2/3)+10*(1/3) = 16.67
    assert get_goal_inflation_rate(session, g) == 16.67


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
    """``sync_imf_cpi_history`` uses :func:`implied_imf_monthly_yoy_pairs` — mock that list."""
    fake_pairs = [("2019-01", 5.85), ("2025-01", 6.0)]
    with patch.object(inf, "implied_imf_monthly_yoy_pairs", return_value=fake_pairs):
        merged = fetch_and_cache_inflation(session)
    assert merged["CPI_GENERAL"] == 6.0  # latest calendar month in mock
    rows = session.exec(
        select(InflationRate).where(InflationRate.category == "CPI_GENERAL")
    ).all()
    assert len(rows) == 2
    assert {r.period for r in rows} == {"2019-01", "2025-01"}
    assert all(r.source == "IMF_SDMX" for r in rows)


def test_fetch_failure_keeps_defaults(session: Session):
    with patch.object(inf, "implied_imf_monthly_yoy_pairs", return_value=None):
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
    fake_pairs = [("2019-03", 4.0), ("2025-06", 6.1)]
    with patch.object(inf, "implied_imf_monthly_yoy_pairs", return_value=fake_pairs):
        r = client.post("/api/inflation/refresh")
    assert r.status_code == 200
    assert r.json()["rates"]["CPI_GENERAL"]["rate"] == 6.1
