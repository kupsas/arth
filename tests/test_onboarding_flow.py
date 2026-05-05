"""
Integration-style tests for the onboarding API surface (Track 2 Phase 6).

Gmail and heavy backfill work stay mocked; we still exercise the FastAPI stack,
:mod:`sqlmodel` session wiring, and JSON contracts for the wizard.
"""

from __future__ import annotations

import importlib
import json
import time
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
from api.models import OnboardingState, Transaction, UserContact, UserSecrets
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

    # Neuter init_db so the lifespan doesn't touch the production SQLite file.
    # The scheduler and startup maintenance are handled by the autouse fixture
    # _neuter_lifespan_side_effects in conftest.py.
    from api import database as _db_mod
    import api.main as _main_mod

    _orig_db_init = _db_mod.init_db
    _orig_main_init = _main_mod.init_db

    def _noop_init() -> None:
        return None

    _db_mod.init_db = _noop_init
    _main_mod.init_db = _noop_init

    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "flow_user"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        _db_mod.init_db = _orig_db_init
        _main_mod.init_db = _orig_main_init
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
                sample_message_ids=["mid1", "mid2"],
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
    time.sleep(0.25)
    with Session(engine) as session:  # type: ignore[call-arg]
        row = session.exec(select(OnboardingState).where(OnboardingState.user_id == "flow_user")).first()
        assert row is not None
        assert "a@test.in" in (row.discovery_results_json or "")
        assert getattr(row, "persist_sources_status", None) == "done"


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
    j0 = s.json()
    assert j0["has_any_api_key"] is False
    assert j0["has_openai_api_key"] is False
    assert j0["has_anthropic_api_key"] is False
    assert j0["has_google_api_key"] is False

    r = flow_client.post(
        "/api/onboarding/api-key", json={"openai_api_key": "sk-insecure-test-xyz"}
    )
    assert r.status_code == 200, r.text

    s2 = flow_client.get("/api/onboarding/classifier-status")
    j2 = s2.json()
    assert j2["has_any_api_key"] is True
    assert j2["has_openai_api_key"] is True
    assert j2["has_anthropic_api_key"] is False
    assert j2["has_google_api_key"] is False
    with Session(engine) as session:  # type: ignore[call-arg]
        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == "flow_user")).first()
        assert row and row.secrets_json and "insecure" in row.secrets_json
    # Teardown: clear the key so a later test does not see process-level env as “filled”
    r_clear = flow_client.post("/api/onboarding/api-key", json={"openai_api_key": ""})
    assert r_clear.status_code == 200
    s3 = flow_client.get("/api/onboarding/classifier-status")
    assert s3.status_code == 200
    j3 = s3.json()
    assert j3["has_any_api_key"] is False
    assert j3["has_openai_api_key"] is False


def test_preclassification_get_returns_saved_raw(
    engine: object,
    flow_client: TestClient,
) -> None:
    """GET /preclassification returns raw fields persisted by POST (wizard resume)."""
    g = flow_client.get("/api/onboarding/preclassification")
    assert g.status_code == 200
    assert g.json() == {
        "first_name": "",
        "last_name": "",
        "extra_aliases": [],
        "account_hints": [],
        "family_names": [],
        "friend_names": [],
    }

    p = flow_client.post(
        "/api/onboarding/preclassification",
        json={
            "first_name": "Sai",
            "last_name": "Kuppa",
            "extra_aliases": ["SK"],
            "account_hints": ["1234", "me@paytm"],
            "family_names": ["Mom", "Anita Devi Sharma"],
            "friend_names": ["Rahul Verma"],
        },
    )
    assert p.status_code == 200, p.text

    g2 = flow_client.get("/api/onboarding/preclassification")
    assert g2.status_code == 200
    d = g2.json()
    assert d["first_name"] == "Sai"
    assert d["last_name"] == "Kuppa"
    assert d["extra_aliases"] == ["SK"]
    assert set(d["account_hints"]) == {"1234", "me@paytm"}
    assert d["family_names"] == ["Mom", "Anita Devi Sharma"]
    assert d["friend_names"] == ["Rahul Verma"]

    with Session(engine) as session:  # type: ignore[call-arg]
        st = session.exec(
            select(OnboardingState).where(OnboardingState.user_id == "flow_user")
        ).first()
        assert st is not None
        raw = json.loads(st.preclassification_raw_json)
        assert raw["first_name"] == "Sai"
        assert raw["last_name"] == "Kuppa"
        assert raw["family_names"] == ["Mom", "Anita Devi Sharma"]
        assert raw["friend_names"] == ["Rahul Verma"]

        contacts = session.exec(
            select(UserContact).where(UserContact.user_id == "flow_user")
        ).all()
        by_rel = {(c.relationship, c.display_name): c for c in contacts}
        assert ("FAMILY", "Mom") in by_rel
        assert ("FAMILY", "Anita Devi Sharma") in by_rel
        assert ("FRIEND", "Rahul Verma") in by_rel
        for c in contacts:
            assert c.contact_source == "ONBOARDING"


def test_backfill_progress_reconciles_stale_needs_classification(
    engine: object,
    flow_client: TestClient,
) -> None:
    """GET progress clears ``needs_classification`` when live unknowns are below the pause threshold."""
    sk = "hdfc_savings"
    blob = {
        "status": "needs_classification",
        "emails_found": 44,
        "emails_processed": 30,
        "transactions_parsed": 631,
        "unknowns_pending": 28,
        "error_message": None,
        "_pending_alert_ids": ["m1", "m2"],
    }
    with Session(engine) as session:  # type: ignore[call-arg]
        session.add(
            OnboardingState(
                user_id="flow_user",
                current_step="backfill",
                backfill_progress_json=json.dumps({sk: blob}),
            )
        )
        session.commit()

    with (
        patch(
            "api.routes.onboarding.count_classification_unknowns",
            return_value=15,
        ),
        patch(
            "api.routes.onboarding.effective_onboarding_unknown_threshold",
            return_value=20,
        ),
    ):
        r = flow_client.get(f"/api/onboarding/backfill/{sk}/progress")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unknowns_pending"] == 15
    assert body["status"] == "processing_alerts"
    assert body["current_phase"] == "alerts"

    with Session(engine) as session:  # type: ignore[call-arg]
        st = session.exec(
            select(OnboardingState).where(OnboardingState.user_id == "flow_user")
        ).one()
        saved = json.loads(st.backfill_progress_json or "{}")[sk]
        assert saved["status"] == "processing_alerts"
        assert saved["unknowns_pending"] == 15
        assert saved["_pending_alert_ids"] == ["m1", "m2"]
