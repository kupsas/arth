"""
Integration-style tests for the onboarding API surface (Track 2 Phase 6).

Gmail and heavy backfill work stay mocked; we still exercise the FastAPI stack,
:mod:`sqlmodel` session wiring, and JSON contracts for the wizard.
"""

from __future__ import annotations

import importlib
import json
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import OnboardingState, Transaction, UserSecrets
from scraper.discovery import DiscoveredSource


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


@pytest.fixture(name="flow_client")
def _flow_client(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    for k in (
        "OPENAI_API_KEY",
        "OPENAI_API_KEY_FOR_CLASSIFIER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY_FOR_CLASSIFIER",
        "GOOGLE_API_KEY",
        "GOOGLE_API_KEY_FOR_CLASSIFIER",
    ):
        monkeypatch.setenv(k, "")

    from api import database as _db_mod

    _orig = _db_mod.init_db
    _db_mod.init_db = lambda: None

    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "flow_user"
    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _orig
    app.dependency_overrides.clear()
    # Reset pipeline LLM state for any module that set ``none`` in tests
    pc = importlib.import_module("pipeline.config")
    monkeypatch.setattr(pc, "LLM_MODEL", "auto", raising=False)


@patch("api.routes.onboarding.get_bank_senders_config", return_value={"a@test.in": {"display_name": "T", "source_type": "savings", "accounts": {}}})
@patch("api.routes.onboarding.discover_sources_iter", autospec=True)
def test_discover_saves_to_onboarding_state(
    mock_iter: Any,
    _bank: Any,
    engine: object,
    flow_client: TestClient,
) -> None:
    mock_iter.return_value = iter(
        [
            DiscoveredSource(
                sender_email="a@test.in",
                display_name="T",
                source_type="savings",
                email_count_estimate=2,
                earliest_email_date=date(2020, 1, 1),
                latest_email_date=date(2020, 6, 1),
            )
        ]
    )
    with patch("api.routes.onboarding._gmail_client_connected", return_value=object()):
        r = flow_client.post("/api/onboarding/discover")
    assert r.status_code == 200, r.text
    lines = [json.loads(line) for line in r.text.strip().split("\n") if line.strip()]
    assert lines[0]["type"] == "start"
    assert lines[0]["total"] == 1
    assert lines[1]["type"] == "found"
    assert lines[1]["index"] == 0
    assert lines[2]["type"] == "done"
    with Session(engine) as session:  # type: ignore[call-arg]
        row = session.exec(select(OnboardingState).where(OnboardingState.user_id == "flow_user")).first()
        assert row is not None
        assert "a@test.in" in (row.discovery_results_json or "")


@patch("api.routes.onboarding._gmail_client_connected", return_value=object())
@patch("api.routes.onboarding.run_onboarding_backfill", autospec=True)
def test_backfill_endpoint_merges_progress_in_state(
    mock_backfill: Any,
    _g: Any,
    engine: object,
    flow_client: TestClient,
) -> None:
    sample_progress = {
        "source": "hdfc_savings",
        "status": "processing",
        "emails_found": 5,
        "emails_processed": 2,
        "transactions_parsed": 2,
        "unknowns_pending": 0,
        "error_message": None,
    }
    mock_backfill.return_value = type("R", (), {"progress": sample_progress})()

    r = flow_client.post(
        "/api/onboarding/backfill/hdfc_savings", json={"chunk_size": 5}
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("emails_found") == 5

    with Session(engine) as session:  # type: ignore[call-arg]
        st = session.exec(
            select(OnboardingState).where(OnboardingState.user_id == "flow_user")
        ).first()
        assert st is not None
        assert "hdfc_savings" in (st.backfill_progress_json or "")


def test_gaps_and_complete_round_trip(
    engine: object,
    flow_client: TestClient,
) -> None:
    """Gaps: uses transaction months; complete: flips :class:`OnboardingState` step."""
    t = Transaction(
        content_hash="flow_gap_1",
        txn_date=date(2021, 1, 5),
        account_id="a1",
        user_id="flow_user",
        source_statement="monthly_test_src",
        source_type="statement",
        direction="OUTFLOW",
        amount=1.0,
        raw_description="x",
    )
    with Session(engine) as session:  # type: ignore[call-arg]
        session.add(t)
        session.add(
            OnboardingState(
                user_id="flow_user",
                current_step="gaps",
            )
        )
        session.commit()

    with patch("api.routes.onboarding.get_bank_senders_config") as mock_b:
        mock_b.return_value = {
            "a@b.com": {
                "display_name": "G",
                "source_type": "savings",
                "expected_cadence": "per_transaction",
                "accounts": {
                    "1": {"source_key": "monthly_test_src", "account_id": "a1"},
                },
            }
        }
        g = flow_client.get("/api/onboarding/gaps")
    assert g.status_code == 200, g.text
    assert g.json()["reports"]

    c = flow_client.post("/api/onboarding/complete")
    assert c.status_code == 200, c.text
    with Session(engine) as session:  # type: ignore[call-arg]
        st = session.exec(
            select(OnboardingState).where(OnboardingState.user_id == "flow_user")
        ).one()
        assert st.current_step == "completed"


def test_classifier_status_and_stored_api_key(
    engine: object,
    flow_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``POST /api-key`` round-trips through ``UserSecrets``; status reflects a key when env is empty."""
    s = flow_client.get("/api/onboarding/classifier-status")
    assert s.status_code == 200
    assert s.json()["has_any_api_key"] is False

    r = flow_client.post(
        "/api/onboarding/api-key", json={"openai_api_key": "sk-insecure-test-xyz"}
    )
    assert r.status_code == 200, r.text

    s2 = flow_client.get("/api/onboarding/classifier-status")
    assert s2.json()["has_any_api_key"] is True
    with Session(engine) as session:  # type: ignore[call-arg]
        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == "flow_user")).first()
        assert row and row.secrets_json and "insecure" in row.secrets_json
    # Teardown: clear the key so a later test does not see process-level env as “filled”
    r_clear = flow_client.post("/api/onboarding/api-key", json={"openai_api_key": ""})
    assert r_clear.status_code == 200
