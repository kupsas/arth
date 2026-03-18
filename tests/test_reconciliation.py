"""
Tests for the DB writer's email-statement reconciliation logic.

Uses an in-memory SQLite database (StaticPool) so each test gets a clean,
isolated DB with no filesystem side-effects.

Five core scenarios from the Phase 4 plan:
  1. Email row + matching statement  → row upgraded to source_type='reconciled', no duplicate
  2. Statement with no email match   → fresh insert as source_type='statement'
  3. Different account, same amount+date → no false positive across accounts
  4. Manual counterparty/category edits preserved through reconciliation
  5. Re-running same data (same content_hash) → zero new rows

These tests only call write_to_db() directly — they don't touch Gmail or the
scraper pipeline.  The goal is to verify the 3-path write logic in isolation.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.models import Transaction
from pipeline.db_writer import write_to_db
from pipeline.models import CanonicalTransaction, Direction


# ─── Shared DB fixtures ───────────────────────────────────────────────────────

@pytest.fixture(name="engine")
def in_memory_engine():
    """Fresh in-memory SQLite for each test.

    StaticPool is critical here: without it, each new Session() gets its own
    empty in-memory database (SQLite's default behaviour).  StaticPool forces
    all connections from this engine to share the same in-memory DB.
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
    """Open Session bound to the in-memory engine."""
    with Session(engine) as s:
        yield s


# ─── Builder helper ───────────────────────────────────────────────────────────

def _txn(
    *,
    account_id: str = "HDFC_SAL_3703",
    amount: Decimal = Decimal("500.00"),
    txn_date: datetime.date = datetime.date(2026, 3, 15),
    raw_description: str = "UPI: swiggy@icici Swiggy",
    direction: Direction = Direction.OUTFLOW,
    ref_number: str | None = None,
) -> CanonicalTransaction:
    """Build a minimal CanonicalTransaction for use in write_to_db() calls.

    Only the fields relevant to reconciliation logic are parameterised.
    txn_id uses a fixed value — that's fine since we're not testing uniqueness.
    """
    return CanonicalTransaction(
        txn_id="T_00000001",
        txn_date=txn_date,
        account_id=account_id,
        source_statement="hdfc_savings",
        direction=direction,
        amount=amount,
        raw_description=raw_description,
        ref_number=ref_number,
    )


# ─── Scenario 1: Email row + matching statement → reconciled ──────────────────

class TestEmailThenStatement:
    """
    The canonical reconciliation flow:
      1. Email scraper inserts a row (source_type='email', is_reviewed=False)
      2. Statement upload arrives with a matching transaction (same account,
         same amount, date within ±1 day but different raw_description)
      3. Expected: the email row is UPGRADED, NOT duplicated.
    """

    def test_single_row_after_reconciliation(self, session):
        """Reconciliation must produce exactly one row, not two."""
        email_txn = _txn(raw_description="UPI: eatclub@icici EatClub")
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="msg_abc",
        )

        # Statement description differs (as it would in real life), but
        # account, amount, and date all match.
        stmt_txn = _txn(raw_description="UPI-EATCLUB-RESTAURANT@ICICI-XXX")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        rows = session.exec(select(Transaction)).all()
        assert len(rows) == 1, (
            "Reconciliation should produce exactly one DB row, not a duplicate"
        )

    def test_source_type_becomes_reconciled(self, session):
        email_txn = _txn()
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="msg_001",
        )
        stmt_txn = _txn(raw_description="STMT-NARRATION-DIFFERENT")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        row = session.exec(select(Transaction)).first()
        assert row.source_type == "reconciled"

    def test_is_reviewed_becomes_true(self, session):
        """Statement arrival auto-promotes an unreviewed email row to reviewed."""
        email_txn = _txn()
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="msg_002",
        )
        stmt_txn = _txn(raw_description="STMT-DESC")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        row = session.exec(select(Transaction)).first()
        assert row.is_reviewed is True

    def test_statement_description_overwrites_email_description(self, session):
        """Statement narration is richer — it should replace the short email alert text."""
        email_txn = _txn(raw_description="UPI: short email desc")
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="msg_003",
        )
        stmt_txn = _txn(raw_description="UPI-DETAILED-STATEMENT-NARRATION")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        row = session.exec(select(Transaction)).first()
        assert row.raw_description == "UPI-DETAILED-STATEMENT-NARRATION"

    def test_gmail_message_id_preserved_after_reconciliation(self, session):
        """The audit trail link back to the original email must survive the upgrade."""
        email_txn = _txn()
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="preserve_me_123",
        )
        stmt_txn = _txn(raw_description="STMT-OVERWRITES")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        row = session.exec(select(Transaction)).first()
        assert row.gmail_message_id == "preserve_me_123", (
            "gmail_message_id must be kept on the row so we can trace it back "
            "to the email that originally captured it"
        )

    def test_reconciliation_works_when_dates_differ_by_one_day(self, session):
        """
        Bank email alerts and statement entries often land on different calendar
        days (e.g. late-night UPI: alert on Mar 15, statement says Mar 16).
        The ±1 day fuzzy window must catch this.
        """
        email_txn = _txn(txn_date=datetime.date(2026, 3, 15))
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="msg_date_edge",
        )
        stmt_txn = _txn(
            txn_date=datetime.date(2026, 3, 16),  # 1 day later
            raw_description="STMT-NEXT-DAY-SETTLEMENT",
        )
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        rows = session.exec(select(Transaction)).all()
        assert len(rows) == 1
        assert rows[0].source_type == "reconciled"


# ─── Scenario 2: Statement with no email match → fresh insert ─────────────────

class TestStatementNoEmailMatch:
    """
    Transactions that never had an email alert (salary inflows, broker trades,
    refunds, etc.) arrive ONLY via statement.  They should be inserted fresh
    as source_type='statement', is_reviewed=True.
    """

    def test_statement_only_row_source_type(self, session):
        stmt_txn = _txn(
            raw_description="NEFT-SALARY-EMPLOYER-INC",
            direction=Direction.INFLOW,
        )
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        rows = session.exec(select(Transaction)).all()
        assert len(rows) == 1
        assert rows[0].source_type == "statement"

    def test_statement_only_row_is_reviewed(self, session):
        stmt_txn = _txn(raw_description="STMT-NO-EMAIL-MATCH")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        row = session.exec(select(Transaction)).first()
        assert row.is_reviewed is True

    def test_pipeline_run_new_count_is_one(self, session):
        stmt_txn = _txn(raw_description="STMT-NEW-COUNT-CHECK")
        run = write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )
        assert run.new_count == 1


# ─── Scenario 3: Different account, same amount+date → no false match ─────────

class TestNoFalsePositiveAcrossAccounts:
    """
    Two different accounts can have coincidentally matching amounts on the same
    day — this must NOT trigger reconciliation.  The account_id is a hard
    equality constraint in the fuzzy match, not an approximation.
    """

    def test_different_accounts_produce_two_rows(self, session):
        # Email row on credit card
        cc_email = _txn(
            account_id="HDFC_CC_1905",
            amount=Decimal("500.00"),
            txn_date=datetime.date(2026, 3, 15),
            raw_description="CC: email row",
        )
        write_to_db(
            [cc_email],
            source_key="hdfc_cc_1905", llm_model="none",
            session=session, source_type="email", gmail_message_id="cc_msg",
        )

        # Statement row on SAVINGS account — same amount, same date, different account
        savings_stmt = _txn(
            account_id="HDFC_SAL_3703",
            amount=Decimal("500.00"),
            txn_date=datetime.date(2026, 3, 15),
            raw_description="UPI-SAVINGS-STATEMENT-ROW",
        )
        write_to_db(
            [savings_stmt],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        rows = session.exec(select(Transaction)).all()
        assert len(rows) == 2, (
            "Transactions on different accounts must not be reconciled against each other"
        )

    def test_accounts_retain_correct_source_types(self, session):
        """After the write, CC email stays 'email' and savings stmt stays 'statement'."""
        cc_email = _txn(
            account_id="HDFC_CC_1905", raw_description="CC: still email",
        )
        write_to_db(
            [cc_email],
            source_key="hdfc_cc_1905", llm_model="none",
            session=session, source_type="email", gmail_message_id="cc_no_match",
        )
        savings_stmt = _txn(
            account_id="HDFC_SAL_3703", raw_description="STMT-SAVINGS-NO-MATCH",
        )
        write_to_db(
            [savings_stmt],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        rows_by_account = {
            r.account_id: r.source_type
            for r in session.exec(select(Transaction)).all()
        }
        assert rows_by_account["HDFC_CC_1905"] == "email"
        assert rows_by_account["HDFC_SAL_3703"] == "statement"


# ─── Scenario 4: Manual edits preserved through reconciliation ─────────────────

class TestManualEditsPreserved:
    """
    If a user manually set counterparty/category on an email-sourced row
    before the statement arrived, those values must survive the upgrade.
    This is the most important UX guarantee in the reconciliation design.
    """

    def test_manual_counterparty_survives_reconciliation(self, session, engine):
        # 1. Email row inserted
        email_txn = _txn(raw_description="UPI: swiggy@icici SomeGenericDesc")
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="manual_edit_msg",
        )

        # 2. Simulate a manual review: user sets counterparty on the row
        with Session(engine) as edit_session:
            row = edit_session.exec(select(Transaction)).first()
            row.counterparty = "Swiggy"
            edit_session.add(row)
            edit_session.commit()

        # 3. Statement arrives with the same transaction
        stmt_txn = _txn(raw_description="UPI-SWIGGY-STMT-NARRATION")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        session.expire_all()  # force a fresh read from DB
        row = session.exec(select(Transaction)).first()
        assert row.counterparty == "Swiggy", (
            "Manual counterparty must not be overwritten by reconciliation"
        )

    def test_manual_category_survives_reconciliation(self, session, engine):
        email_txn = _txn(raw_description="UPI: some@vpa Vendor")
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="cat_edit_msg",
        )

        # User manually sets category
        with Session(engine) as edit_session:
            row = edit_session.exec(select(Transaction)).first()
            row.counterparty_category = "Food & Dining"
            edit_session.add(row)
            edit_session.commit()

        stmt_txn = _txn(raw_description="STMT-OVERWRITES-DESC")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        session.expire_all()
        row = session.exec(select(Transaction)).first()
        assert row.counterparty_category == "Food & Dining", (
            "Manual category must not be overwritten by reconciliation"
        )


# ─── Scenario 5: Content-hash dedup (same data re-inserted) ───────────────────

class TestContentHashDedup:
    """
    Re-running the same statement file (or re-processing the same email) must
    never create duplicate rows.  This is handled by the content_hash fast path
    in write_to_db() — Path A runs before the reconciliation check.
    """

    def test_rerun_same_statement_no_new_rows(self, session):
        stmt_txn = _txn(raw_description="STMT-DEDUP-TEST")
        write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        # Second run — identical data
        run2 = write_to_db(
            [stmt_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="statement",
        )

        rows = session.exec(select(Transaction)).all()
        assert len(rows) == 1, "Same content_hash must not produce a duplicate row"
        assert run2.new_count == 0

    def test_rerun_same_email_no_new_rows(self, session):
        email_txn = _txn(raw_description="UPI: dedup@test DedupTest")
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="dup_msg",
        )

        run2 = write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="dup_msg",
        )

        rows = session.exec(select(Transaction)).all()
        assert len(rows) == 1
        assert run2.new_count == 0

    def test_email_row_is_unreviewed(self, session):
        """Email-sourced rows enter the DB as unreviewed so they surface in the review queue."""
        email_txn = _txn(raw_description="UPI: test@upi EmailUnreviewed")
        write_to_db(
            [email_txn],
            source_key="hdfc_savings", llm_model="none",
            session=session, source_type="email", gmail_message_id="unreviewed_msg",
        )

        row = session.exec(select(Transaction)).first()
        assert row.is_reviewed is False
