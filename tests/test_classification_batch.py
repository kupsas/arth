"""
Unit / API tests for onboarding batch classification (Track 2 Phase 3b).

The FastAPI :func:`~api.routes.onboarding.onboarding_classify` path updates
transactions and optionally inserts :class:`api.models.UserMerchantRule` rows
with ``source=USER_CORRECTION``.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import Transaction, UserMerchantRule
from importlib import import_module


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


@pytest.fixture(name="onboarding_test_client")
def _onboarding_test_client(
    engine: object, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """``TestClient`` with the same in-memory + auth override pattern as ``test_db_and_api``."""
    from api import database as _db_mod

    _orig_init = _db_mod.init_db
    _db_mod.init_db = lambda: None

    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: "batch_user"

    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _orig_init
    app.dependency_overrides.clear()
    # Avoid leaking a lowered LLM threshold into other test modules
    _pc = import_module("pipeline.config")
    monkeypatch.setattr(_pc, "LLM_MODEL", "auto", raising=False)


def _seed_user_and_txn(session: Session) -> int:
    t = Transaction(
        content_hash="batch_hash_01",
        txn_date=datetime.date(2024, 5, 1),
        account_id="ACC",
        user_id="batch_user",
        source_statement="src_a",
        source_type="email",
        direction="OUTFLOW",
        amount=99.0,
        raw_description="UPI SWIGGY SWIGGY",
        channel="UPI",
        is_reviewed=False,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return int(t.id or 0)


def test_onboarding_classify_updates_txn_and_creates_rule(
    engine: object,
    onboarding_test_client: TestClient,
) -> None:
    with Session(engine) as session:
        txn_id = _seed_user_and_txn(session)

    body = {
        "source": "src_a",
        "items": [
            {
                "txn_id": txn_id,
                "counterparty": "Swiggy",
                "counterparty_category": "Swiggy",
                "spend_category": "WANT",
                "apply_to_future": True,
                "merchant_rule_keyword": "SWIGGY",
            }
        ],
    }
    r = onboarding_test_client.post("/api/onboarding/classify", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok"
    assert data["rules_upserted"] == 1

    with Session(engine) as session:
        row = session.get(Transaction, txn_id)
        assert row is not None
        assert row.counterparty == "Swiggy"
        assert row.spend_category == "WANT"
        assert row.classification_source == "USER_REVIEWED"
        assert row.is_reviewed is True
        ur = session.exec(
            select(UserMerchantRule).where(
                UserMerchantRule.user_id == "batch_user",
                UserMerchantRule.keyword == "SWIGGY",
            )
        ).first()
        assert ur is not None
        assert ur.source == "USER_CORRECTION"
        assert ur.display_name == "Swiggy"


def test_onboarding_classify_derives_spend_category_when_omitted(
    engine: object,
    onboarding_test_client: TestClient,
) -> None:
    """POST /classify fills spend_category from rules when the client omits it."""
    with Session(engine) as session:
        t = Transaction(
            content_hash="batch_hash_spend_infer",
            txn_date=datetime.date(2024, 6, 1),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_a",
            source_type="email",
            direction="OUTFLOW",
            amount=120.0,
            raw_description="UPI ZOMATO ZOMATO",
            channel="UPI",
            txn_type="UPI_EXPENSE",
            is_reviewed=False,
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        tid = int(t.id or 0)

    r = onboarding_test_client.post(
        "/api/onboarding/classify",
        json={
            "source": "src_a",
            "items": [
                {
                    "txn_id": tid,
                    "counterparty": "Zomato",
                    "counterparty_category": "Food & Dining",
                    "apply_to_future": False,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    with Session(engine) as session:
        row = session.get(Transaction, tid)
        assert row is not None
        assert row.spend_category == "WANT"


def test_onboarding_classify_leaves_spend_none_for_friends_when_omitted(
    engine: object,
    onboarding_test_client: TestClient,
) -> None:
    """Friends & Family stays without spend (same as rules classifier policy)."""
    with Session(engine) as session:
        t = Transaction(
            content_hash="batch_hash_spend_ff",
            txn_date=datetime.date(2024, 6, 2),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_a",
            source_type="email",
            direction="OUTFLOW",
            amount=50.0,
            raw_description="UPI FRIEND",
            channel="UPI",
            txn_type="UPI_TRANSFER",
            is_reviewed=False,
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        tid = int(t.id or 0)

    r = onboarding_test_client.post(
        "/api/onboarding/classify",
        json={
            "source": "src_a",
            "items": [
                {
                    "txn_id": tid,
                    "counterparty": "Alex",
                    "counterparty_category": "Friends and Family",
                    "apply_to_future": False,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    with Session(engine) as session:
        row = session.get(Transaction, tid)
        assert row is not None
        assert row.spend_category is None


def test_onboarding_classify_without_body_source_accepts_mixed_sources(
    engine: object,
    onboarding_test_client: TestClient,
) -> None:
    """Omit ``source`` on the body — each txn is validated individually (email rows only)."""
    with Session(engine) as session:
        t1 = Transaction(
            content_hash="batch_hash_mixed_01",
            txn_date=datetime.date(2024, 5, 1),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_a",
            source_type="email",
            direction="OUTFLOW",
            amount=10.0,
            raw_description="UPI MERCHANT A",
            channel="UPI",
            txn_type="UPI_EXPENSE",
            is_reviewed=False,
        )
        t2 = Transaction(
            content_hash="batch_hash_mixed_02",
            txn_date=datetime.date(2024, 5, 2),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_b",
            source_type="email",
            direction="OUTFLOW",
            amount=20.0,
            raw_description="UPI MERCHANT B",
            channel="UPI",
            txn_type="UPI_EXPENSE",
            is_reviewed=False,
        )
        session.add(t1)
        session.add(t2)
        session.commit()
        session.refresh(t1)
        session.refresh(t2)
        id1 = int(t1.id or 0)
        id2 = int(t2.id or 0)

    r = onboarding_test_client.post(
        "/api/onboarding/classify",
        json={
            "items": [
                {
                    "txn_id": id1,
                    "counterparty": "Merchant A",
                    "counterparty_category": "Food",
                    "apply_to_future": False,
                },
                {
                    "txn_id": id2,
                    "counterparty": "Merchant B",
                    "counterparty_category": "Food",
                    "apply_to_future": False,
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["updated"] == 2

    with Session(engine) as session:
        r1 = session.get(Transaction, id1)
        r2 = session.get(Transaction, id2)
        assert r1 is not None and r2 is not None
        assert r1.counterparty == "Merchant A"
        assert r2.counterparty == "Merchant B"


def test_onboarding_classify_rejects_wrong_source(
    engine: object,
    onboarding_test_client: TestClient,
) -> None:
    with Session(engine) as session:
        txn_id = _seed_user_and_txn(session)
    r = onboarding_test_client.post(
        "/api/onboarding/classify",
        json={
            "source": "other_source",
            "items": [
                {
                    "txn_id": txn_id,
                    "counterparty": "X",
                    "counterparty_category": "X",
                }
            ],
        },
    )
    assert r.status_code == 400


def test_onboarding_classify_propagates_merchant_keyword_to_sibling_upi_rows(
    engine: object,
    onboarding_test_client: TestClient,
) -> None:
    """A ``USER_CORRECTION`` keyword on UPI narrations should re-classify other email rows in the same request."""
    narr = (
        "UPI/PI-BABUL HUSSAIN LASKAR-babullaskar241@oksbi-SBIN0017401-502337699320-5042 "
        "Triumph Dec Value Dt 23/01/2025 Ref 502337699320"
    )
    narr_b = narr.replace("502337699320", "502337699321")
    with Session(engine) as session:
        t1 = Transaction(
            content_hash="prop_babul_01",
            txn_date=datetime.date(2025, 1, 23),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_a",
            source_type="email",
            direction="OUTFLOW",
            amount=500.0,
            raw_description=narr,
            channel="UPI",
            is_reviewed=False,
        )
        t2 = Transaction(
            content_hash="prop_babul_02",
            txn_date=datetime.date(2025, 1, 24),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_a",
            source_type="email",
            direction="OUTFLOW",
            amount=120.0,
            raw_description=narr_b,
            channel="UPI",
            is_reviewed=False,
        )
        session.add(t1)
        session.add(t2)
        session.commit()
        session.refresh(t1)
        session.refresh(t2)
        id1 = int(t1.id or 0)
        id2 = int(t2.id or 0)

    r = onboarding_test_client.post(
        "/api/onboarding/classify",
        json={
            "source": "src_a",
            "items": [
                {
                    "txn_id": id1,
                    "counterparty": "Babul Hussain Laskar",
                    "counterparty_category": "Friends and Family",
                    "apply_to_future": True,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("auto_propagated", 0) >= 1

    with Session(engine) as session:
        u1 = session.get(Transaction, id1)
        assert u1 is not None
        assert u1.is_reviewed is True
        u2 = session.get(Transaction, id2)
        assert u2 is not None
        assert u2.counterparty == "Babul Hussain Laskar"
        assert u2.counterparty_category == "Friends and Family"
        assert u2.classification_source != "USER_REVIEWED"
        assert u2.is_reviewed is True


def test_onboarding_classify_propagates_multiword_keyword_when_tokens_not_contiguous(
    engine: object,
    onboarding_test_client: TestClient,
) -> None:
    """Propagation matches when each name token appears but the full ``FIRST LAST`` substring does not."""
    # Seed row: no contiguous "NIMISH GUPTA" (hyphen between tokens).
    narr_split = (
        "UPI-nimigup@icici-ICIC0001601-433738967016-NIMISH-GUPTA-Value Dt 02/12/2024 Ref 433738967016"
    )
    narr_anchor = "UPI-NIMISH GUPTA-nimigup@icici-ICIC0001601-433738967017-Value Dt 03/12/2024 Ref 433738967017"
    with Session(engine) as session:
        t_split = Transaction(
            content_hash="prop_nimish_split_01",
            txn_date=datetime.date(2024, 12, 2),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_a",
            source_type="email",
            direction="OUTFLOW",
            amount=50.0,
            raw_description=narr_split,
            channel="UPI",
            txn_type="UPI_EXPENSE",
            is_reviewed=False,
        )
        t_anchor = Transaction(
            content_hash="prop_nimish_split_02",
            txn_date=datetime.date(2024, 12, 3),
            account_id="ACC",
            user_id="batch_user",
            source_statement="src_a",
            source_type="email",
            direction="OUTFLOW",
            amount=51.0,
            raw_description=narr_anchor,
            channel="UPI",
            txn_type="UPI_EXPENSE",
            is_reviewed=False,
        )
        session.add(t_split)
        session.add(t_anchor)
        session.commit()
        session.refresh(t_split)
        session.refresh(t_anchor)
        id_split = int(t_split.id or 0)
        id_anchor = int(t_anchor.id or 0)

    assert "NIMISH GUPTA" not in narr_split.upper()

    r = onboarding_test_client.post(
        "/api/onboarding/classify",
        json={
            "source": "src_a",
            "items": [
                {
                    "txn_id": id_anchor,
                    "counterparty": "Nimish Gupta",
                    "counterparty_category": "Friends and Family",
                    "apply_to_future": True,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("auto_propagated", 0) >= 1

    with Session(engine) as session:
        u_split = session.get(Transaction, id_split)
        assert u_split is not None
        assert u_split.counterparty == "Nimish Gupta"
        assert u_split.counterparty_category == "Friends and Family"
