"""Tests for reminder ↔ transaction fingerprint matching and settings validation."""

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
from api.reminder_matching import (
    AMOUNT_TOLERANCE,
    compute_reminder_month_status,
    decode_example_transaction_ids,
    encode_example_transaction_ids,
    month_date_range,
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
    app.dependency_overrides[get_current_user] = lambda: "u"

    import api.database as _db_mod

    _original_init = _db_mod.init_db
    _db_mod.init_db = lambda: None

    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _original_init
    app.dependency_overrides.clear()


def _txn(
    session: Session,
    *,
    txn_date: datetime.date,
    amount: float,
    counterparty: str | None = "Landlord",
    direction: str = "OUTFLOW",
    exclude_from_analytics: bool = False,
    content_hash: str | None = None,
    user_id: str = "u",
) -> Transaction:
    h = content_hash or f"h_{txn_date.isoformat()}_{amount}_{counterparty}"
    t = Transaction(
        content_hash=h,
        txn_date=txn_date,
        account_id="ACC1",
        user_id=user_id,
        source_statement="s",
        direction=direction,
        amount=amount,
        currency="INR",
        txn_type="UPI_EXPENSE",
        channel="UPI",
        counterparty=counterparty,
        counterparty_category="Rent & Housing",
        raw_description="pay",
        exclude_from_analytics=exclude_from_analytics,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


class TestCodecAndMonth:
    def test_encode_decode_roundtrip(self):
        assert decode_example_transaction_ids(None) == []
        assert decode_example_transaction_ids("") == []
        assert decode_example_transaction_ids(encode_example_transaction_ids([1, 2])) == [
            1,
            2,
        ]
        assert encode_example_transaction_ids([]) is None

    def test_month_date_range(self):
        a, b = month_date_range("2026-02")
        assert a == datetime.date(2026, 2, 1)
        assert b == datetime.date(2026, 2, 28)

    def test_month_invalid(self):
        with pytest.raises(ValueError):
            month_date_range("2026-13")


class TestComputeReminderMonthStatus:
    def test_no_examples(self, session: Session):
        r = Reminder(
            user_id="u",
            name="Rent",
            due_day_of_month=5,
            example_transaction_ids=None,
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        df = datetime.date(2026, 3, 1)
        dt = datetime.date(2026, 3, 31)
        out = compute_reminder_month_status(session, r, df, dt)
        assert out["has_mapping"] is False
        assert out["unmapped_reason"] == "no_examples"

    def test_matched_this_month(self, session: Session):
        ex = _txn(
            session,
            txn_date=datetime.date(2025, 12, 1),
            amount=10000.0,
            counterparty="My Landlord",
        )
        r = Reminder(
            user_id="u",
            name="Rent",
            due_day_of_month=5,
            amount=10000.0,
            example_transaction_ids=encode_example_transaction_ids([ex.id]),
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        _txn(
            session,
            txn_date=datetime.date(2026, 3, 5),
            amount=10000.0,
            counterparty="My Landlord",
            content_hash="march_pay",
        )
        out = compute_reminder_month_status(
            session,
            r,
            datetime.date(2026, 3, 1),
            datetime.date(2026, 3, 31),
        )
        assert out["has_mapping"] is True
        assert out["matched_this_month"] is True
        assert len(out["matched_transactions"]) == 1

    def test_wrong_amount_band(self, session: Session):
        ex = _txn(
            session,
            txn_date=datetime.date(2025, 12, 1),
            amount=10000.0,
            counterparty="My Landlord",
        )
        r = Reminder(
            user_id="u",
            name="Rent",
            due_day_of_month=5,
            amount=10000.0,
            example_transaction_ids=encode_example_transaction_ids([ex.id]),
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        # Too far from median 10000: outside ±AMOUNT_TOLERANCE
        bad_amt = 10000.0 * (1.0 + AMOUNT_TOLERANCE + 0.05)
        _txn(
            session,
            txn_date=datetime.date(2026, 3, 5),
            amount=bad_amt,
            counterparty="My Landlord",
            content_hash="march_bad",
        )
        out = compute_reminder_month_status(
            session,
            r,
            datetime.date(2026, 3, 1),
            datetime.date(2026, 3, 31),
        )
        assert out["matched_this_month"] is False
        assert out["unmapped_reason"] == "no_match_yet"

    def test_reminder_amount_floor(self, session: Session):
        ex = _txn(
            session,
            txn_date=datetime.date(2025, 12, 1),
            amount=10000.0,
            counterparty="X",
        )
        r = Reminder(
            user_id="u",
            name="Rent",
            due_day_of_month=5,
            amount=10000.0,
            example_transaction_ids=encode_example_transaction_ids([ex.id]),
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        _txn(
            session,
            txn_date=datetime.date(2026, 3, 5),
            amount=5000.0,
            counterparty="X",
            content_hash="small",
        )
        out = compute_reminder_month_status(
            session,
            r,
            datetime.date(2026, 3, 1),
            datetime.date(2026, 3, 31),
        )
        assert out["matched_this_month"] is False

    def test_excluded_from_analytics_not_matched(self, session: Session):
        ex = _txn(
            session,
            txn_date=datetime.date(2025, 12, 1),
            amount=10000.0,
            counterparty="My Landlord",
        )
        r = Reminder(
            user_id="u",
            name="Rent",
            due_day_of_month=5,
            example_transaction_ids=encode_example_transaction_ids([ex.id]),
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        _txn(
            session,
            txn_date=datetime.date(2026, 3, 5),
            amount=10000.0,
            counterparty="My Landlord",
            content_hash="excl",
            exclude_from_analytics=True,
        )
        out = compute_reminder_month_status(
            session,
            r,
            datetime.date(2026, 3, 1),
            datetime.date(2026, 3, 31),
        )
        assert out["matched_this_month"] is False

    def test_examples_stale(self, session: Session):
        r = Reminder(
            user_id="u",
            name="Rent",
            due_day_of_month=5,
            example_transaction_ids=encode_example_transaction_ids([99999]),
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        out = compute_reminder_month_status(
            session,
            r,
            datetime.date(2026, 3, 1),
            datetime.date(2026, 3, 31),
        )
        assert out["examples_stale"] is True
        assert out["has_mapping"] is False


class TestSettingsAPI:
    def test_reject_inflow_example(self, client: TestClient, session: Session):
        t = _txn(
            session,
            txn_date=datetime.date(2026, 1, 1),
            amount=100.0,
            direction="INFLOW",
            counterparty="Salary",
        )
        r = client.post(
            "/api/settings/reminders",
            json={
                "name": "X",
                "due_day_of_month": 1,
                "example_transaction_ids": [t.id],
            },
        )
        assert r.status_code == 400

    def test_reject_missing_counterparty(self, client: TestClient, session: Session):
        t = Transaction(
            content_hash="nocp",
            txn_date=datetime.date(2026, 1, 1),
            account_id="ACC1",
            user_id="u",
            source_statement="s",
            direction="OUTFLOW",
            amount=100.0,
            currency="INR",
            txn_type="UPI_EXPENSE",
            channel="UPI",
            counterparty=None,
            counterparty_category=None,
            raw_description="x",
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        r = client.post(
            "/api/settings/reminders",
            json={
                "name": "X",
                "due_day_of_month": 1,
                "example_transaction_ids": [t.id],
            },
        )
        assert r.status_code == 400

    def test_status_endpoint(self, client: TestClient, session: Session):
        ex = _txn(
            session,
            txn_date=datetime.date(2026, 1, 1),
            amount=5000.0,
            counterparty="Bill Co",
        )
        client.post(
            "/api/settings/reminders",
            json={
                "name": "Utilities",
                "due_day_of_month": 10,
                "example_transaction_ids": [ex.id],
            },
        )
        _txn(
            session,
            txn_date=datetime.date(2026, 3, 8),
            amount=5000.0,
            counterparty="Bill Co",
            content_hash="mar_bill",
        )
        r = client.get("/api/settings/reminders/status", params={"month": "2026-03"})
        assert r.status_code == 200
        body = r.json()
        assert body["month"] == "2026-03"
        assert len(body["items"]) == 1
        assert body["items"][0]["matched_this_month"] is True
