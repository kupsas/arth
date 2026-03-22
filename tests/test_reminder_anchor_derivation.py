"""Tests for auto-derived description anchors on reminders."""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import Reminder, Transaction
from api.reminder_anchor_derivation import (
    derive_description_anchors,
    encode_description_match_anchors,
)
from api.reminder_matching import (
    compute_reminder_month_status,
    encode_example_transaction_ids,
)


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


def _row(
    session: Session,
    *,
    raw: str,
    amount: float = 1000.0,
    counterparty: str = "HDFC Credit Card",
    content_hash: str,
    txn_date: datetime.date | None = None,
    txn_type: str = "UPI_EXPENSE",
) -> Transaction:
    td = txn_date or datetime.date(2026, 1, 1)
    t = Transaction(
        content_hash=content_hash,
        txn_date=td,
        account_id="ACC1",
        source_statement="s",
        direction="OUTFLOW",
        amount=amount,
        currency="INR",
        txn_type=txn_type,
        channel="UPI",
        counterparty=counterparty,
        counterparty_category=None,
        raw_description=raw,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


class TestDeriveDescriptionAnchors:
    def test_hdfc_two_masks_intersection(self, session: Session):
        a = _row(
            session,
            raw="IB BILLPAY DR-HDFC4U-526873XXXXXX1905",
            content_hash="h1",
        )
        b = _row(
            session,
            raw="IB BILLPAY DR-HDFC4U-526873XXXXXX1905-EXTRA",
            content_hash="h2",
        )
        assert "526873XXXXXX1905" in derive_description_anchors([a, b])

    def test_second_card_distinct(self, session: Session):
        a = _row(
            session,
            raw="IB BILLPAY DR-HDFC97-361010XXXX5778",
            content_hash="c1",
        )
        b = _row(
            session,
            raw="IB BILLPAY DR-HDFC97-361010XXXX5778",
            content_hash="c2",
        )
        d = derive_description_anchors([a, b])
        assert any("5778" in x for x in d)

    def test_mixed_rows_no_single_regex_token(self, session: Session):
        """Still get something via LCS when regex intersection empty."""
        a = _row(session, raw="ACME_WIDGET_PAYMENT_REF_ABC12X", content_hash="m1")
        b = _row(session, raw="ACME_WIDGET_PAYMENT_REF_ABC12Y", content_hash="m2")
        # No shared masked pattern; LCS may be ACME_WIDGET_PAYMENT_REF_ (generic check)
        derive_description_anchors([a, b])  # should not raise


class TestDeriveAnchorsAPI:
    def test_endpoint_returns_mask(self, client: TestClient, session: Session):
        a = _row(
            session,
            raw="IB BILLPAY DR-HDFC4U-526873XXXXXX1905",
            content_hash="api1",
        )
        r = client.post(
            "/api/settings/reminders/derive-anchors",
            json={"transaction_ids": [a.id]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert any("1905" in x for x in body["anchors"])


class TestAnchorMatchingSkipsAmount:
    def test_large_amount_still_matches_with_anchor(self, session: Session):
        ex = _row(
            session,
            raw="IB BILLPAY DR-HDFC4U-526873XXXXXX1905",
            amount=50_000.0,
            content_hash="ex1",
        )
        r = Reminder(
            user_id="u",
            name="CC",
            due_day_of_month=15,
            example_transaction_ids=encode_example_transaction_ids([ex.id]),
            description_match_anchors=encode_description_match_anchors(
                ["526873XXXXXX1905"]
            ),
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        _row(
            session,
            raw="IB BILLPAY DR-HDFC4U-526873XXXXXX1905",
            amount=500_000.0,
            counterparty="HDFC Credit Card",
            content_hash="huge",
            txn_date=datetime.date(2026, 3, 10),
        )
        out = compute_reminder_month_status(
            session,
            r,
            datetime.date(2026, 3, 1),
            datetime.date(2026, 3, 31),
        )
        assert out["matched_this_month"] is True

    def test_card_payment_type_included_in_candidates(self, session: Session):
        """CC bill pay from savings is CARD_PAYMENT — must still match reminders."""
        ex = _row(
            session,
            raw="IB BILLPAY DR-HDFC4U-526873XXXXXX1905",
            amount=50_000.0,
            content_hash="ex_cp",
            txn_type="CARD_PAYMENT",
        )
        rem = Reminder(
            user_id="u",
            name="CC",
            due_day_of_month=15,
            example_transaction_ids=encode_example_transaction_ids([ex.id]),
            description_match_anchors=encode_description_match_anchors(
                ["526873XXXXXX1905"]
            ),
        )
        session.add(rem)
        session.commit()
        session.refresh(rem)
        _row(
            session,
            raw="IB BILLPAY DR-HDFC4U-526873XXXXXX1905",
            amount=91_275.0,
            counterparty="HDFC Credit Card",
            content_hash="mar_cp",
            txn_date=datetime.date(2026, 3, 1),
            txn_type="CARD_PAYMENT",
        )
        out = compute_reminder_month_status(
            session,
            rem,
            datetime.date(2026, 3, 1),
            datetime.date(2026, 3, 31),
        )
        assert out["matched_this_month"] is True
