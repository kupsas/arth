"""
Tests for Phase 2: SQLite database operations and FastAPI endpoints.

Uses an in-memory SQLite database so tests are fast, isolated, and don't
touch any real data.  The FastAPI TestClient makes synchronous HTTP calls
without starting a real server.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.auth import get_current_user
from api.main import app
from api.database import get_session
from api.models import PipelineRun, Transaction, UserPipelineSource
from pipeline.db_writer import compute_content_hash, write_to_db
from pipeline.models import (
    CanonicalTransaction,
    Channel,
    CounterpartyCategory,
    Direction,
    TxnType,
)


# ───────────────────────────────────────────────────────────────────────────
# Fixtures — in-memory SQLite, overridden session, TestClient
# ───────────────────────────────────────────────────────────────────────────

@pytest.fixture(name="engine")
def in_memory_engine():
    """Create a fresh in-memory SQLite engine for each test.

    StaticPool ensures every connection from this engine shares the same
    in-memory database (by default, each connection to "sqlite://" gets
    its own independent DB — not what we want for tests).
    """
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
    """Yield a Session bound to the in-memory engine."""
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def api_client(engine):
    """FastAPI TestClient with the DB session overridden to use in-memory SQLite.

    We also need to create the tables on the test engine because the app's
    lifespan init_db() runs against the production engine, not our test one.
    The in_memory_engine fixture already calls create_all(), so tables exist.
    We just need to wire the session override so all API requests hit them.
    """
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    # Bypass auth for all existing API tests — they test DB/API logic, not auth.
    # Auth-specific behaviour is tested in TestAuth below using a separate fixture.
    # Must match seeded Transaction.user_id and default account→user mapping.
    app.dependency_overrides[get_current_user] = lambda: "local"

    # Neuter init_db so the lifespan doesn't touch the production SQLite file.
    # The scheduler and startup maintenance are handled by the autouse fixture
    # _neuter_lifespan_side_effects in conftest.py.
    import api.database as _db_mod
    import api.main as _main_mod

    _orig_db_init = _db_mod.init_db
    _orig_main_init = _main_mod.init_db

    _db_mod.init_db = lambda: None
    _main_mod.init_db = lambda: None

    with TestClient(app) as c:
        yield c

    _db_mod.init_db = _orig_db_init
    _main_mod.init_db = _orig_main_init
    app.dependency_overrides.clear()


# ───────────────────────────────────────────────────────────────────────────
# Factory helpers — build CanonicalTransaction and DB rows easily
# ───────────────────────────────────────────────────────────────────────────

def _make_canonical(
    *,
    txn_date: str = "2025-03-15",
    raw_description: str = "UPI/123456/Swiggy/sbi@ybl",
    amount: str = "450.00",
    account_id: str = "HDFC_SAL_3703",
    direction: Direction = Direction.OUTFLOW,
    txn_type: TxnType | None = TxnType.UPI_EXPENSE,
    channel: Channel | None = Channel.UPI,
    counterparty: str | None = "Swiggy",
    counterparty_category: CounterpartyCategory | None = CounterpartyCategory.SWIGGY,
) -> CanonicalTransaction:
    return CanonicalTransaction(
        txn_id="T_00000001",
        txn_date=datetime.date.fromisoformat(txn_date),
        account_id=account_id,
        source_statement="HDFC_Statement.txt",
        direction=direction,
        amount=Decimal(amount),
        currency="INR",
        txn_type=txn_type,
        channel=channel,
        counterparty=counterparty,
        counterparty_category=counterparty_category,
        raw_description=raw_description,
    )


def _seed_db_transaction(session: Session, **overrides) -> Transaction:
    """Insert a Transaction row directly (bypassing the pipeline)."""
    defaults = {
        "content_hash": "abc123",
        "txn_date": datetime.date(2025, 3, 15),
        "account_id": "HDFC_SAL_3703",
        "source_statement": "HDFC_Statement.txt",
        "direction": "OUTFLOW",
        "amount": 450.0,
        "currency": "INR",
        "txn_type": "UPI_EXPENSE",
        "channel": "UPI",
        "counterparty": "Swiggy",
        "counterparty_category": "Swiggy",
        "raw_description": "UPI/123456/Swiggy/sbi@ybl",
        "is_reviewed": True,
        "user_id": "local",
    }
    defaults.update(overrides)
    txn = Transaction(**defaults)
    session.add(txn)
    session.commit()
    session.refresh(txn)
    return txn


# ═══════════════════════════════════════════════════════════════════════════
# DB Writer Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestContentHash:
    def test_deterministic(self):
        """Same input always produces the same hash."""
        txn = _make_canonical()
        assert compute_content_hash(txn) == compute_content_hash(txn)

    def test_different_amount_different_hash(self):
        """Changing any component of the natural key changes the hash."""
        txn_a = _make_canonical(amount="450.00")
        txn_b = _make_canonical(amount="451.00")
        assert compute_content_hash(txn_a) != compute_content_hash(txn_b)

    def test_different_date_different_hash(self):
        txn_a = _make_canonical(txn_date="2025-03-15")
        txn_b = _make_canonical(txn_date="2025-03-16")
        assert compute_content_hash(txn_a) != compute_content_hash(txn_b)


class TestWriteToDb:
    def test_basic_insert(self, session: Session):
        """A single transaction is inserted with correct fields."""
        txn = _make_canonical()
        run = write_to_db([txn], source_key="hdfc_savings", llm_model="none", session=session)

        assert run.status == "completed"
        assert run.txn_count == 1
        assert run.new_count == 1
        assert run.txn_date_min == datetime.date(2025, 3, 15)
        assert run.txn_date_max == datetime.date(2025, 3, 15)

        # Verify the row is in the DB
        db_txn = session.get(Transaction, 1)
        assert db_txn is not None
        assert db_txn.counterparty == "Swiggy"
        assert db_txn.amount == 450.0
        assert db_txn.direction == "OUTFLOW"

    def test_dedup_skips_existing(self, session: Session):
        """Re-inserting the same transaction is a no-op (idempotent)."""
        txn = _make_canonical()
        run_1 = write_to_db([txn], source_key="hdfc_savings", llm_model="none", session=session)
        run_2 = write_to_db([txn], source_key="hdfc_savings", llm_model="none", session=session)

        assert run_1.new_count == 1
        assert run_2.new_count == 0       # skipped as duplicate
        assert run_2.updated_count == 0   # all fields already filled
        assert run_2.txn_count == 1       # still processed 1

    def test_backfill_fills_nulls_without_overwriting(self, session: Session):
        """Re-running with richer data fills NULL fields but never overwrites existing values."""
        # First run: insert with no counterparty or category (simulates rules-only)
        txn_sparse = _make_canonical(
            counterparty=None,
            counterparty_category=None,
            txn_type=None,
        )
        run_1 = write_to_db([txn_sparse], source_key="hdfc_savings", llm_model="none", session=session)
        assert run_1.new_count == 1

        db_txn = session.get(Transaction, 1)
        assert db_txn.counterparty is None
        assert db_txn.counterparty_category is None
        assert db_txn.txn_type is None

        # Second run: same transaction but now with LLM-filled fields
        txn_rich = _make_canonical(
            counterparty="Swiggy",
            counterparty_category=CounterpartyCategory.SWIGGY,
            txn_type=TxnType.UPI_EXPENSE,
        )
        run_2 = write_to_db([txn_rich], source_key="hdfc_savings", llm_model="auto", session=session)

        assert run_2.new_count == 0       # not a new insert
        assert run_2.updated_count == 1   # one row was backfilled

        session.refresh(db_txn)
        assert db_txn.counterparty == "Swiggy"
        assert db_txn.counterparty_category == "Swiggy"
        assert db_txn.txn_type == "UPI_EXPENSE"

    def test_backfill_does_not_overwrite_existing_values(self, session: Session):
        """Backfill must not clobber a value that was already set (e.g. manual edit)."""
        txn_original = _make_canonical(counterparty="Swiggy")
        write_to_db([txn_original], source_key="hdfc_savings", llm_model="none", session=session)

        # Simulate a re-run where the LLM returns a different counterparty
        txn_different = _make_canonical(counterparty="Zomato")
        run_2 = write_to_db([txn_different], source_key="hdfc_savings", llm_model="auto", session=session)

        assert run_2.updated_count == 0  # nothing to backfill — already filled

        db_txn = session.get(Transaction, 1)
        assert db_txn.counterparty == "Swiggy"  # original value preserved

    def test_different_txns_both_inserted(self, session: Session):
        """Two distinct transactions both get inserted."""
        txn_a = _make_canonical(raw_description="UPI/111/Swiggy", amount="100.00")
        txn_b = _make_canonical(raw_description="UPI/222/Zomato", amount="200.00")
        run = write_to_db([txn_a, txn_b], source_key="hdfc_savings", llm_model="none", session=session)

        assert run.new_count == 2
        assert run.txn_count == 2

    def test_pipeline_run_linked(self, session: Session):
        """Transaction row is linked to the correct PipelineRun."""
        txn = _make_canonical()
        run = write_to_db([txn], source_key="hdfc_savings", llm_model="none", session=session)

        db_txn = session.get(Transaction, 1)
        assert db_txn.pipeline_run_id == run.id


# ═══════════════════════════════════════════════════════════════════════════
# API Endpoint Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestHealthCheck:
    def test_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestTransactionList:
    def test_empty_db(self, client: TestClient):
        """Empty DB returns zero items with correct pagination metadata."""
        resp = client.get("/api/transactions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1

    def test_returns_seeded_data(self, client: TestClient, session: Session):
        """Seeded transactions show up in the list endpoint."""
        _seed_db_transaction(session, content_hash="hash1", counterparty="Swiggy")
        _seed_db_transaction(session, content_hash="hash2", counterparty="Zomato")

        resp = client.get("/api/transactions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_filter_by_direction(self, client: TestClient, session: Session):
        _seed_db_transaction(session, content_hash="h1", direction="OUTFLOW")
        _seed_db_transaction(session, content_hash="h2", direction="INFLOW")

        resp = client.get("/api/transactions", params={"direction": "INFLOW"})
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["direction"] == "INFLOW"

    def test_filter_by_date_range(self, client: TestClient, session: Session):
        _seed_db_transaction(session, content_hash="h1", txn_date=datetime.date(2025, 1, 15))
        _seed_db_transaction(session, content_hash="h2", txn_date=datetime.date(2025, 6, 15))

        resp = client.get("/api/transactions", params={
            "date_from": "2025-05-01",
            "date_to": "2025-12-31",
        })
        data = resp.json()
        assert data["total"] == 1

    def test_search_text(self, client: TestClient, session: Session):
        _seed_db_transaction(session, content_hash="h1", counterparty="Swiggy", raw_description="UPI/Swiggy")
        _seed_db_transaction(session, content_hash="h2", counterparty="Zomato", raw_description="UPI/Zomato")

        resp = client.get("/api/transactions", params={"search": "swiggy"})
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["counterparty"] == "Swiggy"

    def test_pagination(self, client: TestClient, session: Session):
        for i in range(5):
            _seed_db_transaction(session, content_hash=f"h{i}")

        resp = client.get("/api/transactions", params={"page": 1, "page_size": 2})
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["total_pages"] == 3


class TestTransactionUserIsolation:
    """Rows for another Arth user must not appear in list or single-txn reads."""

    def test_list_and_get_hide_other_users_rows(self, client: TestClient, session: Session):
        mine = _seed_db_transaction(session, content_hash="iso_mine", user_id="local")
        theirs = _seed_db_transaction(
            session,
            content_hash="iso_theirs",
            user_id="bob",
            account_id="OTHER_ACC",
        )
        lst = client.get("/api/transactions")
        assert lst.status_code == 200
        ids = {row["id"] for row in lst.json()["items"]}
        assert mine.id in ids
        assert theirs.id not in ids
        assert client.get(f"/api/transactions/{theirs.id}").status_code == 404


class TestTransactionGetOne:
    def test_found(self, client: TestClient, session: Session):
        txn = _seed_db_transaction(session)
        resp = client.get(f"/api/transactions/{txn.id}")
        assert resp.status_code == 200
        assert resp.json()["counterparty"] == "Swiggy"

    def test_not_found(self, client: TestClient):
        resp = client.get("/api/transactions/9999")
        assert resp.status_code == 404


class TestTransactionPatch:
    def test_update_counterparty(self, client: TestClient, session: Session):
        txn = _seed_db_transaction(session)
        resp = client.patch(
            f"/api/transactions/{txn.id}",
            json={"counterparty": "Zomato", "counterparty_category": "Food & Dining"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["counterparty"] == "Zomato"
        assert data["counterparty_category"] == "Food & Dining"

    def test_update_not_found(self, client: TestClient):
        resp = client.patch("/api/transactions/9999", json={"notes": "test"})
        assert resp.status_code == 404

    def test_update_preserves_other_fields(self, client: TestClient, session: Session):
        """Updating one field doesn't clobber others."""
        txn = _seed_db_transaction(session, counterparty="Swiggy", notes="original")
        resp = client.patch(
            f"/api/transactions/{txn.id}",
            json={"notes": "updated"},
        )
        data = resp.json()
        assert data["notes"] == "updated"
        assert data["counterparty"] == "Swiggy"  # untouched


class TestBulkUpdate:
    def test_bulk_update(self, client: TestClient, session: Session):
        t1 = _seed_db_transaction(session, content_hash="h1", is_reviewed=True)
        t2 = _seed_db_transaction(session, content_hash="h2", is_reviewed=True)

        resp = client.patch("/api/transactions/bulk", json={
            "ids": [t1.id, t2.id],
            "update": {"is_reviewed": False},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["updated"]) == {t1.id, t2.id}
        assert data["not_found"] == []

    def test_bulk_update_partial_not_found(self, client: TestClient, session: Session):
        t1 = _seed_db_transaction(session, content_hash="h1")
        resp = client.patch("/api/transactions/bulk", json={
            "ids": [t1.id, 9999],
            "update": {"notes": "bulk test"},
        })
        data = resp.json()
        assert data["updated"] == [t1.id]
        assert data["not_found"] == [9999]


class TestPipelineRunsList:
    def test_empty(self, client: TestClient):
        resp = client.get("/api/pipeline/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_runs(self, client: TestClient, session: Session):
        run = PipelineRun(source_key="hdfc_savings", llm_model="none", status="completed")
        session.add(run)
        session.commit()

        resp = client.get("/api/pipeline/runs")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["source_key"] == "hdfc_savings"
        assert data[0]["status"] == "completed"


class TestPipelineRunDetail:
    def test_found(self, client: TestClient, session: Session):
        run = PipelineRun(source_key="icici_savings", llm_model="auto", status="running")
        session.add(run)
        session.commit()
        session.refresh(run)

        resp = client.get(f"/api/pipeline/runs/{run.id}")
        assert resp.status_code == 200
        assert resp.json()["source_key"] == "icici_savings"

    def test_not_found(self, client: TestClient):
        resp = client.get("/api/pipeline/runs/9999")
        assert resp.status_code == 404


class TestPipelineTrigger:
    def test_invalid_source(self, client: TestClient):
        resp = client.post("/api/pipeline/run", json={"source_key": "nope"})
        assert resp.status_code == 400

    def test_valid_source_returns_run_ids(self, client: TestClient, session: Session):
        """Triggering a valid source returns run IDs (the background thread
        will fail because we don't have real data files, but the API response
        itself should be immediate and correct)."""
        # Source configs are now DB-driven; seed one so the route can validate
        # the requested source_key and return 200 before the background job runs.
        session.add(UserPipelineSource(
            user_id="local",
            source_key="hdfc_savings",
            account_id="HDFC_SAL_3703",
            currency="INR",
            statement_folder="HDFC_Savings",
        ))
        session.commit()

        resp = client.post("/api/pipeline/run", json={
            "source_key": "hdfc_savings",
            "llm_model": "none",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["run_ids"]) == 1
        assert "Import started" in data["message"]


# ───────────────────────────────────────────────────────────────────────────
# Q11: Negative surplus months endpoint tests
# ───────────────────────────────────────────────────────────────────────────

class TestNegativeSurplusMonths:
    """Tests for GET /api/metrics/negative-surplus-months (Q11)."""

    def test_empty_db_returns_zero_deficits(self, client: TestClient):
        """When there are no transactions at all, deficit count must be 0."""
        resp = client.get("/api/metrics/negative-surplus-months")
        assert resp.status_code == 200
        data = resp.json()
        assert data["months_with_deficit"] == 0
        assert data["total_deficit"] == 0.0
        assert data["deficit_months"] == []

    def test_surplus_month_not_counted(self, client: TestClient, session: Session):
        """A month where income > expense must NOT appear in deficit_months."""
        today = datetime.date.today()
        this_month = today.replace(day=15)

        # ₹1000 income this month
        _seed_db_transaction(
            session, content_hash="q11_in",
            direction="INFLOW", amount=1000.0,
            txn_type="INCOME_SALARY", txn_date=this_month,
        )
        # ₹500 expense this month (income still wins)
        _seed_db_transaction(
            session, content_hash="q11_out",
            direction="OUTFLOW", amount=500.0,
            txn_type="UPI_EXPENSE", txn_date=this_month,
        )

        resp = client.get("/api/metrics/negative-surplus-months")
        assert resp.status_code == 200
        data = resp.json()
        assert data["months_with_deficit"] == 0

    def test_deficit_month_detected(self, client: TestClient, session: Session):
        """A month where expense > income must appear in deficit_months."""
        today = datetime.date.today()
        this_month = today.replace(day=15)

        # ₹500 income, ₹1200 expense → net = -700 → deficit
        _seed_db_transaction(
            session, content_hash="q11_low_in",
            direction="INFLOW", amount=500.0,
            txn_type="INCOME_SALARY", txn_date=this_month,
        )
        _seed_db_transaction(
            session, content_hash="q11_high_out",
            direction="OUTFLOW", amount=1200.0,
            txn_type="UPI_EXPENSE", txn_date=this_month,
        )

        resp = client.get("/api/metrics/negative-surplus-months")
        assert resp.status_code == 200
        data = resp.json()
        assert data["months_with_deficit"] == 1
        assert data["total_deficit"] == pytest.approx(700.0, abs=0.01)
        assert len(data["deficit_months"]) == 1
        # The net must be negative (expense > income)
        assert data["deficit_months"][0]["net"] < 0

    def test_self_transfers_excluded(self, client: TestClient, session: Session):
        """SELF_TRANSFER and CARD_PAYMENT rows must not affect the net calculation."""
        today = datetime.date.today()
        this_month = today.replace(day=15)

        # Real income ₹1000
        _seed_db_transaction(
            session, content_hash="q11_real_in",
            direction="INFLOW", amount=1000.0,
            txn_type="INCOME_SALARY", txn_date=this_month,
        )
        # Real expense ₹400
        _seed_db_transaction(
            session, content_hash="q11_real_out",
            direction="OUTFLOW", amount=400.0,
            txn_type="UPI_EXPENSE", txn_date=this_month,
        )
        # SELF_TRANSFER should be excluded from both sides
        _seed_db_transaction(
            session, content_hash="q11_self",
            direction="OUTFLOW", amount=5000.0,
            txn_type="SELF_TRANSFER", txn_date=this_month,
        )
        # CARD_PAYMENT should also be excluded from expenses
        _seed_db_transaction(
            session, content_hash="q11_card_pay",
            direction="OUTFLOW", amount=3000.0,
            txn_type="CARD_PAYMENT", txn_date=this_month,
        )

        # Net = 1000 - 400 = +600 → no deficit
        resp = client.get("/api/metrics/negative-surplus-months")
        assert resp.status_code == 200
        data = resp.json()
        assert data["months_with_deficit"] == 0

    def test_months_param_respected(self, client: TestClient, session: Session):
        """months=1 should only look at the current month."""
        today = datetime.date.today()
        this_month = today.replace(day=15)

        # deficit THIS month
        _seed_db_transaction(
            session, content_hash="q11_cur_out",
            direction="OUTFLOW", amount=999.0,
            txn_type="UPI_EXPENSE", txn_date=this_month,
        )

        resp = client.get("/api/metrics/negative-surplus-months", params={"months": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_months"] == 1
        assert data["months_with_deficit"] == 1


class TestOnboardingGoalTemplates:
    """GET /api/onboarding/goal-templates — PIT vs recurring listing + previews."""

    def test_lists_template_sections_and_loan_emi(self, client: TestClient):
        resp = client.get("/api/onboarding/goal-templates")
        assert resp.status_code == 200
        payload = resp.json()
        assert "template_sections" in payload
        sec_classes = [s["goal_class"] for s in payload["template_sections"]]
        assert "POINT_IN_TIME" in sec_classes
        assert "RECURRING_CASH_FLOW" in sec_classes
        ids = {t["id"] for t in payload["templates"]}
        assert "loan_emi" in ids
        assert "travel" in ids
        n_pit = sum(1 for t in payload["templates"] if t["goal_class"] == "POINT_IN_TIME")
        n_rec = sum(1 for t in payload["templates"] if t["goal_class"] == "RECURRING_CASH_FLOW")
        assert n_pit >= 1
        assert n_rec >= 2

    def test_recurring_preview_uses_emi_copy_for_loan(self, client: TestClient):
        r = client.get(
            "/api/onboarding/goal-templates",
            params={"target_amount": 50_000, "years": 5, "template_id": "loan_emi"},
        )
        assert r.status_code == 200
        loan = next(t for t in r.json()["templates"] if t["id"] == "loan_emi")
        assert loan.get("preview")
        assert loan["preview"]["preview_mechanism"] == "RECURRING_CASH_FLOW"
        assert "EMI" in loan["preview"]["copy"]

    def test_pit_preview_for_house(self, client: TestClient):
        r = client.get(
            "/api/onboarding/goal-templates",
            params={"target_amount": 5_000_000, "years": 8, "template_id": "house"},
        )
        assert r.status_code == 200
        house = next(t for t in r.json()["templates"] if t["id"] == "house")
        assert house.get("preview")
        assert house["preview"]["preview_mechanism"] == "POINT_IN_TIME"
        assert "Target" in house["preview"]["copy"]

    def test_headline_previews_when_no_template_selected(self, client: TestClient):
        r = client.get(
            "/api/onboarding/goal-templates",
            params={"target_amount": 1_000_000, "years": 4},
        )
        assert r.status_code == 200
        body = r.json()
        assert "headline_preview" in body
        assert "headline_preview_recurring" in body
        assert body["headline_preview"]["preview_mechanism"] == "POINT_IN_TIME"
        assert body["headline_preview_recurring"]["preview_mechanism"] == "RECURRING_CASH_FLOW"


# ───────────────────────────────────────────────────────────────────────────
# Auth tests — uses a raw TestClient with NO dependency overrides so we
# exercise the real authentication code path.
# ───────────────────────────────────────────────────────────────────────────

@pytest.fixture(name="unauthed_client")
def unauthed_api_client(engine):
    """TestClient with DB overridden; real ``get_current_user`` (no credential gate)."""
    def _override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    import api.database as _db_mod
    import api.main as _main_mod

    _orig_db_init = _db_mod.init_db
    _orig_main_init = _main_mod.init_db

    _db_mod.init_db = lambda: None
    _main_mod.init_db = lambda: None

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    _db_mod.init_db = _orig_db_init
    _main_mod.init_db = _orig_main_init
    app.dependency_overrides.clear()


class TestAuth:
    def test_protected_endpoint_allows_without_cookie(self, unauthed_client: TestClient):
        """Local installs do not require a session cookie."""
        resp = unauthed_client.get("/api/transactions")
        assert resp.status_code == 200

    def test_health_is_public(self, unauthed_client: TestClient):
        """/health must be reachable without a session."""
        resp = unauthed_client.get("/health")
        assert resp.status_code == 200

    def test_login_sets_cookie(self, unauthed_client: TestClient):
        """POST /api/auth/login always succeeds for local installs."""
        resp = unauthed_client.post(
            "/api/auth/login",
            json={"username": "anyone", "password": "anything"},
        )
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True
        assert resp.json()["username"] == "local"
        assert "arth_session" in resp.cookies

    def test_me_without_login_returns_local_user(self, unauthed_client: TestClient):
        """GET /api/auth/me works without a cookie."""
        resp = unauthed_client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["username"] == "local"

    def test_me_after_login_still_local(self, unauthed_client: TestClient):
        unauthed_client.post(
            "/api/auth/login",
            json={"username": "legacy", "password": "ignored"},
        )
        resp = unauthed_client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["username"] == "local"

    def test_transactions_after_logout(self, unauthed_client: TestClient):
        """Logout clears cookie but routes remain reachable without it."""
        unauthed_client.post(
            "/api/auth/login",
            json={"username": "legacy", "password": "ignored"},
        )
        unauthed_client.post("/api/auth/logout")
        resp = unauthed_client.get("/api/transactions")
        assert resp.status_code == 200

    def test_internal_agent_header_optional(self, unauthed_client: TestClient):
        """X-Arth-Internal remains accepted for agent clients (identity is still local)."""
        from api.auth import agent_internal_token

        tok = agent_internal_token()
        resp = unauthed_client.get(
            "/api/transactions",
            headers={"X-Arth-Internal": tok},
        )
        assert resp.status_code == 200

    def test_internal_agent_header_wrong_secret_still_ok_without_gate(self, unauthed_client: TestClient):
        resp = unauthed_client.get(
            "/api/transactions",
            headers={"X-Arth-Internal": "not-a-valid-token"},
        )
        assert resp.status_code == 200
