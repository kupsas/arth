"""
Integration-style tests for the onboarding API surface (Track 2 Phase 6).

Gmail and heavy backfill work stay mocked; we still exercise the FastAPI stack,
:mod:`sqlmodel` session wiring, and JSON contracts for the wizard.
"""

from __future__ import annotations

import importlib
import json
import logging
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
from api.models import Holding, InvestmentTransaction, OnboardingState, Transaction, UserContact, UserSecrets
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod
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


@patch("api.routes.onboarding.get_bank_senders_config", return_value={"a@test.in": {"display_name": "T", "instrument_type": "savings", "accounts": {}}})
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
                instrument_type="savings",
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


@patch("api.routes.onboarding._gmail_client_connected", return_value=object())
@patch("api.routes.onboarding.run_onboarding_backfill", autospec=True)
def test_backfill_stream_returns_sse_complete_event(
    mock_backfill: Any,
    _g: Any,
    flow_client: TestClient,
) -> None:
    """GET …/stream wraps orchestrator output as SSE (terminal ``complete`` frame)."""
    sample_progress = {
        "source": "hdfc_savings",
        "status": "complete",
        "emails_found": 2,
        "emails_processed": 2,
        "transactions_parsed": 2,
        "unknowns_pending": 0,
        "error_message": None,
        "current_phase": None,
    }
    mock_backfill.return_value = type("R", (), {"progress": sample_progress})()

    r = flow_client.get("/api/onboarding/backfill/hdfc_savings/stream")
    assert r.status_code == 200, r.text
    assert "text/event-stream" in (r.headers.get("content-type") or "").lower()
    body = r.text
    assert "data:" in body
    assert '"type": "complete"' in body or '"type":"complete"' in body.replace(" ", "")


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
                "instrument_type": "savings",
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


def test_patch_onboarding_state_logs_step_transition(
    flow_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PATCH /state emits INFO on real step changes and DEBUG with raw ids; idempotent PATCH is quiet."""
    caplog.set_level(logging.DEBUG, logger="api.routes.onboarding")
    init = flow_client.get("/api/onboarding/state")
    assert init.status_code == 200
    assert init.json()["current_step"] == "welcome"

    caplog.clear()
    r = flow_client.patch("/api/onboarding/state", json={"current_step": "discovery"})
    assert r.status_code == 200, r.text
    infos = [rec.message for rec in caplog.records if rec.levelno == logging.INFO]
    assert any("Finding your accounts from email" in m for m in infos)
    debugs = [rec.message for rec in caplog.records if rec.levelno == logging.DEBUG]
    assert any("'welcome'" in m and "'discovery'" in m for m in debugs)

    caplog.clear()
    again = flow_client.patch("/api/onboarding/state", json={"current_step": "discovery"})
    assert again.status_code == 200
    assert not [rec for rec in caplog.records if rec.levelno == logging.INFO]


def test_onboarding_complete_clears_review_queue_for_user_data(
    engine: object,
    flow_client: TestClient,
) -> None:
    """POST /complete marks remaining bank + linked investment rows reviewed for this user."""
    with Session(engine) as session:  # type: ignore[call-arg]
        session.merge(OnboardingState(user_id="flow_user", current_step="summary"))
        session.add(
            Transaction(
                content_hash="complete_review_q_01",
                txn_date=date(2024, 6, 1),
                account_id="ACC",
                user_id="flow_user",
                source_statement="sk",
                source_type="email",
                direction="OUTFLOW",
                amount=10.0,
                raw_description="onboarding complete review sweep",
                is_reviewed=False,
            )
        )
        h = Holding(
            name="Inv Test",
            symbol="INV1",
            asset_class=AssetClass.EQUITY.value,
            account_platform="ICICI",
            valuation_method=ValuationMethod.MANUAL.value,
            liquidity_class=LiquidityClass.T_PLUS_1.value,
            user_id="flow_user",
        )
        session.add(h)
        session.commit()
        session.refresh(h)
        session.add(
            InvestmentTransaction(
                txn_date=date(2024, 6, 2),
                symbol="INV1",
                txn_type=InvestmentTxnType.BUY.value,
                quantity=1.0,
                price_per_unit=100.0,
                total_amount=100.0,
                account_platform="ICICI",
                holding_id=h.id,
                is_reviewed=False,
                source_type="email",
            )
        )
        session.commit()

    r = flow_client.post("/api/onboarding/complete")
    assert r.status_code == 200, r.text

    with Session(engine) as session:  # type: ignore[call-arg]
        txn = session.exec(
            select(Transaction).where(Transaction.content_hash == "complete_review_q_01")
        ).one()
        assert txn.is_reviewed is True
        inv = session.exec(
            select(InvestmentTransaction).where(InvestmentTransaction.symbol == "INV1")
        ).one()
        assert inv.is_reviewed is True


def test_onboarding_complete_logs_finished_step(
    engine: object,
    flow_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """POST /complete logs transition into the finished state."""
    caplog.set_level(logging.DEBUG, logger="api.routes.onboarding")
    with Session(engine) as session:  # type: ignore[call-arg]
        session.merge(OnboardingState(user_id="flow_user", current_step="summary"))
        session.commit()

    r = flow_client.post("/api/onboarding/complete")
    assert r.status_code == 200, r.text
    infos = [rec.message for rec in caplog.records if rec.levelno == logging.INFO]
    assert any("First-time setup finished" in m for m in infos)


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
    # Teardown: remove secrets row so later tests do not see stored keys as “filled”
    with Session(engine) as session:  # type: ignore[call-arg]
        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == "flow_user")).first()
        if row:
            session.delete(row)
            session.commit()


def test_classifier_api_key_rejects_clearing_last_stored_key(
    engine: object,
    flow_client: TestClient,
) -> None:
    """Cannot POST an empty key when it would remove the user's last saved classifier key."""
    r = flow_client.post(
        "/api/onboarding/api-key", json={"openai_api_key": "sk-insecure-test-xyz"}
    )
    assert r.status_code == 200, r.text
    r_clear = flow_client.post("/api/onboarding/api-key", json={"openai_api_key": ""})
    assert r_clear.status_code == 400, r_clear.text
    s = flow_client.get("/api/onboarding/classifier-status")
    assert s.status_code == 200
    assert s.json()["has_any_api_key"] is True
    with Session(engine) as session:  # type: ignore[call-arg]
        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == "flow_user")).first()
        if row:
            session.delete(row)
            session.commit()


def test_classifier_api_key_allows_clear_when_another_remains(
    engine: object,
    flow_client: TestClient,
) -> None:
    """Removing one stored provider is OK when another classifier key remains."""
    google_39 = "AIza" + "Sy" + ("x" * 33)
    assert len(google_39) == 39
    assert flow_client.post(
        "/api/onboarding/api-key", json={"openai_api_key": "sk-insecure-test-xyz"}
    ).status_code == 200
    assert flow_client.post(
        "/api/onboarding/api-key", json={"google_api_key": google_39}
    ).status_code == 200
    r_clear = flow_client.post("/api/onboarding/api-key", json={"openai_api_key": ""})
    assert r_clear.status_code == 200, r_clear.text
    s = flow_client.get("/api/onboarding/classifier-status").json()
    assert s["has_openai_api_key"] is False
    assert s["has_google_api_key"] is True
    assert s["has_any_api_key"] is True
    with Session(engine) as session:  # type: ignore[call-arg]
        row = session.exec(select(UserSecrets).where(UserSecrets.user_id == "flow_user")).first()
        if row:
            session.delete(row)
            session.commit()


def test_classifier_api_key_rejects_misshapen_google_key(flow_client: TestClient) -> None:
    """Shape validation rejects obviously wrong Google keys before persist."""
    r = flow_client.post(
        "/api/onboarding/api-key",
        json={"google_api_key": "AIza-short"},
    )
    assert r.status_code == 400, r.text


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
    """GET progress clears ``needs_classification`` once the **global** review queue is empty."""
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
            return_value=0,
        ),
        patch(
            "api.routes.onboarding.count_all_classification_unknowns",
            return_value=0,
        ),
    ):
        r = flow_client.get(f"/api/onboarding/backfill/{sk}/progress")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unknowns_pending"] == 0
    assert body["status"] == "processing_alerts"
    assert body["current_phase"] == "alerts"

    with Session(engine) as session:  # type: ignore[call-arg]
        st = session.exec(
            select(OnboardingState).where(OnboardingState.user_id == "flow_user")
        ).one()
        saved = json.loads(st.backfill_progress_json or "{}")[sk]
        assert saved["status"] == "processing_alerts"
        assert saved["unknowns_pending"] == 0
        assert saved["_pending_alert_ids"] == ["m1", "m2"]


def test_onboarding_has_data_empty(flow_client: TestClient) -> None:
    r = flow_client.get("/api/onboarding/has-data")
    assert r.status_code == 200, r.text
    assert r.json() == {"has_transactions": False, "transaction_count": 0}


def test_onboarding_has_data_with_transactions(engine: object, flow_client: TestClient) -> None:
    t = Transaction(
        content_hash="has_data_chk",
        txn_date=date(2022, 6, 1),
        account_id="acct1",
        user_id="flow_user",
        source_statement="stmt_src",
        source_type="statement",
        direction="INFLOW",
        amount=100.0,
        raw_description="test row",
    )
    with Session(engine) as session:  # type: ignore[call-arg]
        session.add(t)
        session.commit()

    r = flow_client.get("/api/onboarding/has-data")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_transactions"] is True
    assert body["transaction_count"] == 1


def test_onboarding_has_data_mock_env_returns_zero_without_truth(
    monkeypatch: pytest.MonkeyPatch,
    engine: object,
    flow_client: TestClient,
) -> None:
    """``ARTH_MOCK_ONBOARDING_*`` forces zeros while rows still exist (local QA)."""
    monkeypatch.setenv("ARTH_MOCK_ONBOARDING_ZERO_HAS_DATA", "1")
    monkeypatch.setenv("ARTH_MOCK_ONBOARDING_ZERO_HAS_DATA_USERS", "flow_user")
    t = Transaction(
        content_hash="mock_has_data_chk",
        txn_date=date(2022, 6, 2),
        account_id="acct1",
        user_id="flow_user",
        source_statement="stmt_src",
        source_type="statement",
        direction="INFLOW",
        amount=50.0,
        raw_description="still in db",
    )
    with Session(engine) as session:  # type: ignore[call-arg]
        session.add(t)
        session.commit()

    r = flow_client.get("/api/onboarding/has-data")
    assert r.status_code == 200, r.text
    assert r.json() == {"has_transactions": False, "transaction_count": 0}


def test_onboarding_has_data_mock_env_truth_returns_real_count(
    monkeypatch: pytest.MonkeyPatch,
    engine: object,
    flow_client: TestClient,
) -> None:
    monkeypatch.setenv("ARTH_MOCK_ONBOARDING_ZERO_HAS_DATA", "1")
    monkeypatch.setenv("ARTH_MOCK_ONBOARDING_ZERO_HAS_DATA_USERS", "flow_user")
    t = Transaction(
        content_hash="mock_truth_chk",
        txn_date=date(2022, 6, 3),
        account_id="acct1",
        user_id="flow_user",
        source_statement="stmt_src",
        source_type="statement",
        direction="INFLOW",
        amount=25.0,
        raw_description="truth bypass",
    )
    with Session(engine) as session:  # type: ignore[call-arg]
        session.add(t)
        session.commit()

    r = flow_client.get("/api/onboarding/has-data?truth=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_transactions"] is True
    assert body["transaction_count"] == 1


def test_onboarding_has_data_mock_env_wrong_user_unaffected(
    monkeypatch: pytest.MonkeyPatch,
    engine: object,
    flow_client: TestClient,
) -> None:
    monkeypatch.setenv("ARTH_MOCK_ONBOARDING_ZERO_HAS_DATA", "1")
    monkeypatch.setenv("ARTH_MOCK_ONBOARDING_ZERO_HAS_DATA_USERS", "someone_else")
    t = Transaction(
        content_hash="mock_other_user_chk",
        txn_date=date(2022, 6, 4),
        account_id="acct1",
        user_id="flow_user",
        source_statement="stmt_src",
        source_type="statement",
        direction="INFLOW",
        amount=10.0,
        raw_description="row",
    )
    with Session(engine) as session:  # type: ignore[call-arg]
        session.add(t)
        session.commit()

    r = flow_client.get("/api/onboarding/has-data")
    assert r.status_code == 200, r.text
    assert r.json()["transaction_count"] == 1
