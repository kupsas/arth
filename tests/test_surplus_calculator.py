"""
Tests for Sub-Plan B — surplus calculator service and /api/surplus routes.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.auth import get_current_user
from api.database import get_session
from api.main import app
from api.models import RecurringPattern, Transaction
from api.services.account_user_map import clear_test_overrides, register_account_for_user
from api.services.surplus_calculator import compute_surplus


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
    app.dependency_overrides[get_current_user] = lambda: "sashank"

    import api.database as _db_mod

    _original_init = _db_mod.init_db
    _db_mod.init_db = lambda: None

    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _original_init
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_account_map():
    clear_test_overrides()
    yield
    clear_test_overrides()


def _txn(
    *,
    content_hash: str,
    txn_date: datetime.date,
    amount: float,
    account_id: str = "ACC_SASH",
    direction: str = "OUTFLOW",
    counterparty_category: str | None = "Food & Dining",
    spend_category: str | None = "NEED",
    txn_type: str | None = "UPI_EXPENSE",
) -> Transaction:
    return Transaction(
        content_hash=content_hash,
        txn_date=txn_date,
        account_id=account_id,
        user_id="sashank",
        source_statement="t.csv",
        direction=direction,
        amount=amount,
        txn_type=txn_type,
        counterparty="X",
        counterparty_category=counterparty_category,
        raw_description="raw",
        spend_category=spend_category,
    )


def _patch_today_april_2026():
    """Last 3 months from 2026-04-15 → 2026-02, 2026-03, 2026-04."""
    fixed_today = datetime.date(2026, 4, 15)

    class PatchedDate(datetime.date):
        """``today()`` frozen to 2026-04-15; construction unchanged."""

        @classmethod
        def today(cls):
            return fixed_today

    return patch("api.services.query_helpers.datetime.date", PatchedDate), patch(
        "api.services.surplus_calculator.datetime.date", PatchedDate
    )


def test_basic_surplus_path_a(session: Session):
    """Recurring income minus Path A expenses; median across months."""
    register_account_for_user("ACC_SASH", "sashank")
    # Salary pattern
    session.add(
        RecurringPattern(
            user_id="sashank",
            counterparty="Employer",
            direction="INFLOW",
            expected_amount=100_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 4, 1),
            is_active=True,
        )
    )
    # Same rent each month in window (3 months) — Path A
    for i, ym in enumerate([(2026, 2), (2026, 3), (2026, 4)]):
        y, m = ym
        session.add(
            _txn(
                content_hash=f"h{i}a",
                txn_date=datetime.date(y, m, 5),
                amount=20_000.0,
                counterparty_category="Rent & Housing",
                spend_category="NEED",
            )
        )
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        out = compute_surplus(session, "sashank", months=3)

    assert out.monthly_income == 100_000.0
    assert out.months_analyzed == 3
    # 100k - 20k = 80k each month Path A
    assert all(m.surplus_path_a == 80_000.0 for m in out.month_details)
    assert out.monthly_surplus == out.surplus_path_a == out.surplus_path_b


def test_category_filtering_excludes_travel(session: Session):
    """Travel & Stay outflows must not count toward Path A baseline."""
    register_account_for_user("ACC_SASH", "sashank")
    session.add(
        RecurringPattern(
            user_id="sashank",
            counterparty="Co",
            direction="INFLOW",
            expected_amount=50_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 4, 1),
            is_active=True,
        )
    )
    session.add(
        _txn(
            content_hash="t1",
            txn_date=datetime.date(2026, 4, 10),
            amount=99_999.0,
            counterparty_category="Travel & Stay",
            spend_category="WANT",
        )
    )
    session.add(
        _txn(
            content_hash="t2",
            txn_date=datetime.date(2026, 4, 11),
            amount=5_000.0,
            counterparty_category="Food & Dining",
            spend_category="NEED",
        )
    )
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        out = compute_surplus(session, "sashank", months=3)

    # Path A for April: only Food 5000 (not Travel)
    apr = next(m for m in out.month_details if m.month == "2026-04")
    assert apr.expense_category_filtered == 5_000.0


def test_median_smoothing_not_mean(session: Session):
    """Median of [10k, 20k, 30k] surplus gap should be 20k, not 20k mean (same here)."""
    register_account_for_user("ACC_SASH", "sashank")
    session.add(
        RecurringPattern(
            user_id="sashank",
            counterparty="Co",
            direction="INFLOW",
            expected_amount=100_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 4, 1),
            is_active=True,
        )
    )
    # Three months: expenses 10k, 20k, 30k → surplus_a 90k, 80k, 70k → median 80k
    amounts = [10_000.0, 20_000.0, 30_000.0]
    for i, ym in enumerate([(2026, 2), (2026, 3), (2026, 4)]):
        y, m = ym
        session.add(
            _txn(
                content_hash=f"med{i}",
                txn_date=datetime.date(y, m, 8),
                amount=amounts[i],
                counterparty_category="Food & Dining",
                spend_category="NEED",
            )
        )
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        out = compute_surplus(session, "sashank", months=3)

    assert out.surplus_path_a == 80_000.0


def test_dual_path_conservative_min(session: Session):
    """Path A ignores Shopping (not in recurring categories); Path B counts WANT."""
    register_account_for_user("ACC_SASH", "sashank")
    session.add(
        RecurringPattern(
            user_id="sashank",
            counterparty="Co",
            direction="INFLOW",
            expected_amount=100_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 4, 1),
            is_active=True,
        )
    )
    for i, ym in enumerate([(2026, 2), (2026, 3), (2026, 4)]):
        y, m = ym
        session.add(
            _txn(
                content_hash=f"d{i}",
                txn_date=datetime.date(y, m, 3),
                amount=10_000.0,
                counterparty_category="Food & Dining",
                spend_category="NEED",
            )
        )
        session.add(
            _txn(
                content_hash=f"w{i}",
                txn_date=datetime.date(y, m, 4),
                amount=50_000.0,
                counterparty_category="Shopping & E-commerce",
                spend_category="WANT",
            )
        )
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        out = compute_surplus(session, "sashank", months=3)

    # Path A: 100k - 10k Food = 90k. Path B: 100k - 10k NEED - 50k median WANT = 40k.
    assert out.surplus_path_a == 90_000.0
    assert out.surplus_path_b == 40_000.0
    assert out.monthly_surplus == 40_000.0  # min(90k,40k) each month → median 40k


def test_no_income_warning(session: Session):
    register_account_for_user("ACC_SASH", "sashank")
    session.add(
        _txn(
            content_hash="n1",
            txn_date=datetime.date(2026, 4, 1),
            amount=100.0,
            counterparty_category="Food & Dining",
            spend_category="NEED",
        )
    )
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        out = compute_surplus(session, "sashank", months=3)

    assert out.monthly_income == 0.0
    assert any("INFLOW" in w or "income" in w.lower() for w in out.warnings)


def test_user_isolation_patterns(session: Session):
    register_account_for_user("ACC_S", "sashank")
    register_account_for_user("ACC_A", "aditi")
    session.add(
        RecurringPattern(
            user_id="sashank",
            counterparty="S Co",
            direction="INFLOW",
            expected_amount=80_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 4, 1),
            is_active=True,
        )
    )
    session.add(
        RecurringPattern(
            user_id="aditi",
            counterparty="A Co",
            direction="INFLOW",
            expected_amount=40_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 4, 1),
            is_active=True,
        )
    )
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        s = compute_surplus(session, "sashank", months=3)
        a = compute_surplus(session, "aditi", months=3)

    assert s.monthly_income == 80_000.0
    assert a.monthly_income == 40_000.0


def test_api_surplus_endpoint(client: TestClient, session: Session):
    register_account_for_user("ACC_SASH", "sashank")
    session.add(
        RecurringPattern(
            user_id="sashank",
            counterparty="Co",
            direction="INFLOW",
            expected_amount=60_000.0,
            frequency="MONTHLY",
            last_seen_date=datetime.date(2026, 4, 1),
            is_active=True,
        )
    )
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        r = client.get("/api/surplus?months=3&user_id=sashank")

    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == "sashank"
    assert "monthly_surplus" in data
    assert "month_details" in data
    assert len(data["month_details"]) == 3


def test_api_surplus_monthly_endpoint(client: TestClient, session: Session):
    register_account_for_user("ACC_SASH", "sashank")
    session.commit()

    p1, p2 = _patch_today_april_2026()
    with p1, p2:
        r = client.get("/api/surplus/monthly?months=3")

    assert r.status_code == 200
    data = r.json()
    assert "month_details" in data
    assert "monthly_income" not in data
