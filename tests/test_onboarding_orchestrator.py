"""Unit tests for ``scraper.onboarding_orchestrator`` (chunk backfill, thresholds).

Uses in-memory SQLite plus mocks for Gmail and email parsing so nothing hits the
network. This mirrors the strategy in ``test_orchestrator.py``.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.models import Transaction
from scraper.config_loader import BankSendersConfig
from scraper.gmail_client import GmailMessage
from scraper.onboarding_orchestrator import (
    count_all_classification_unknowns,
    count_classification_unknowns,
    run_onboarding_backfill,
    sender_emails_for_source_key,
    pause_backfill_state,
    resume_backfill_state,
    _collect_pending_queue,
)

# Minimal static config shaped like ``BANK_SENDERS`` (one savings source).
_MINI_BANK: BankSendersConfig = {
    "alerts@hdfcbank.net": {
        "display_name": "HDFC",
        "source_type": "savings",
        "expected_cadence": "monthly",
        "accounts": {
            "3703": {"account_id": "HDFC_SAL_3703", "source_key": "hdfc_savings_test"},
        },
    },
    "alerts@hdfcbank.bank.in": {
        "display_name": "HDFC",
        "source_type": "savings",
        "expected_cadence": "per_transaction",
        "accounts": {
            "3703": {"account_id": "HDFC_SAL_3703", "source_key": "hdfc_savings_test"},
        },
    },
}


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
def _session(engine: object):
    with Session(engine) as s:
        yield s


def test_sender_emails_for_source_key_sorted_unique() -> None:
    out = sender_emails_for_source_key(_MINI_BANK, "hdfc_savings_test")
    assert out == [
        "alerts@hdfcbank.bank.in",
        "alerts@hdfcbank.net",
    ]


def _make_unknown_email_txn(
    *,
    i: int,
    user: str = "u1",
    source: str = "hdfc_savings_test",
) -> Transaction:
    """A row that still counts as a classification 'unknown' (OUTFLOW, spend unset)."""
    h = f"u{i:060x}"  # unique 64+ hex for content_hash
    return Transaction(
        content_hash="h" + h,
        txn_date=datetime.date(2024, 1, 1),
        account_id="HDFC_SAL_3703",
        user_id=user,
        source_statement=source,
        source_type="email",
        direction="OUTFLOW",
        amount=1.0,
        raw_description=f"UPI {i}",
        txn_type="UPI_EXPENSE",
        channel="UPI",
        counterparty=None,  # unknown → counted
        counterparty_category=None,
    )


def test_count_all_classification_unknowns_sums_across_sources(
    session: Session,
) -> None:
    session.add(_make_unknown_email_txn(i=1, source="hdfc_savings_test"))
    session.add(_make_unknown_email_txn(i=2, source="icici_savings_test"))
    session.commit()
    assert count_classification_unknowns(session, user_id="u1", source_key="hdfc_savings_test") == 1
    assert count_classification_unknowns(session, user_id="u1", source_key="icici_savings_test") == 1
    assert count_all_classification_unknowns(session, user_id="u1") == 2


def test_count_classification_unknowns_increments(
    session: Session,
) -> None:
    session.add(_make_unknown_email_txn(i=1))
    session.add(_make_unknown_email_txn(i=2))
    session.commit()
    n = count_classification_unknowns(session, user_id="u1", source_key="hdfc_savings_test")
    assert n == 2


@patch("scraper.onboarding_orchestrator.get_bank_senders_config", return_value=_MINI_BANK)
@patch("scraper.onboarding_orchestrator._process_email", return_value=("processed", 1))
def test_run_onboarding_backfill_processes_chunk(
    _proc,
    _bank,
    session: Session,
) -> None:
    """One chunk drains messages, updates progress, then completes the queue."""
    t0 = datetime.date(2010, 1, 1)
    t1 = datetime.date.today() + datetime.timedelta(days=1)
    m1 = GmailMessage(
        id="mid1",
        thread_id="th1",
        sender="alerts@hdfcbank.net",
        subject="x",
        received_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
    )
    m2 = GmailMessage(
        id="mid2",
        thread_id="th2",
        sender="alerts@hdfcbank.net",
        subject="x",
        received_at=datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc),
    )

    class _FakeGmail:
        def search_messages(self, query: str, **kwargs):
            assert "after:" in query
            # Statement PDF sender (.net) — monthly cadence in _MINI_BANK
            if "alerts@hdfcbank.net" in query and "bank.in" not in query:
                return [m1, m2]
            # InstaAlerts deferred until after statements — searched on transition
            return []

        def fetch_message_by_id(self, message_id: str) -> GmailMessage:
            return m1 if message_id == "mid1" else m2

    with patch("scraper.onboarding_orchestrator._record_email"), patch(
        "scraper.onboarding_orchestrator._get_processed_ids", return_value=set()
    ):
        g = _FakeGmail()
        r1 = run_onboarding_backfill(
            session=session,
            user_id="u1",
            source_key="hdfc_savings_test",
            gmail_client=g,  # type: ignore[arg-type]
            existing_progress={},
            chunk_size=1,
            after=t0,
            before=t1,
            unknown_threshold=10_000,
        )
    p1 = r1.progress
    assert p1.get("emails_found") == 2
    assert p1.get("emails_processed") == 1
    assert p1.get("status") == "processing_statements"

    with patch("scraper.onboarding_orchestrator._record_email"), patch(
        "scraper.onboarding_orchestrator._get_processed_ids", return_value=set()
    ):
        r2 = run_onboarding_backfill(
            session=session,
            user_id="u1",
            source_key="hdfc_savings_test",
            gmail_client=g,  # type: ignore[arg-type]
            existing_progress=p1,
            chunk_size=1,
            after=t0,
            before=t1,
            unknown_threshold=10_000,
        )
    p2 = r2.progress
    assert p2.get("emails_processed") == 2
    assert p2.get("status") == "complete"


@patch("scraper.onboarding_orchestrator.get_bank_senders_config", return_value=_MINI_BANK)
@patch("scraper.onboarding_orchestrator._process_email", return_value=("processed", 0))
def test_run_onboarding_backfill_pauses_on_unknown_threshold(
    _proc,
    _bank,
    session: Session,
) -> None:
    """When DB unknowns for the source meet ``unknown_threshold``, status is ``needs_classification``."""
    t0 = datetime.date(2010, 1, 1)
    t1 = datetime.date.today() + datetime.timedelta(days=1)
    m1 = GmailMessage(
        id="sole",
        thread_id="th",
        sender="alerts@hdfcbank.net",
        subject="s",
        received_at=datetime.datetime(2024, 6, 1, tzinfo=datetime.timezone.utc),
    )
    for i in range(3):
        session.add(_make_unknown_email_txn(i=i))
    session.commit()

    class _FakeGmail:
        def search_messages(self, query: str, **kwargs):
            if "alerts@hdfcbank.bank.in" in query:
                return []
            return [m1]

        def fetch_message_by_id(self, _mid: str) -> GmailMessage:
            return m1

    g = _FakeGmail()
    with patch("scraper.onboarding_orchestrator._record_email"), patch(
        "scraper.onboarding_orchestrator._get_processed_ids", return_value=set()
    ):
        r = run_onboarding_backfill(
            session=session,
            user_id="u1",
            source_key="hdfc_savings_test",
            gmail_client=g,  # type: ignore[arg-type]
            existing_progress={},
            chunk_size=1,
            after=t0,
            before=t1,
            unknown_threshold=3,
        )
    assert r.progress.get("status") == "needs_classification"
    assert int(r.progress.get("unknowns_pending") or 0) >= 3


def test_merge_hdfc_cc_statement_senders_enables_statement_first_partition() -> None:
    """InstaAlerts-only DB mapping for hdfc_cc_XXXX still yields CC statement senders."""
    from scraper.onboarding_orchestrator import (
        _partition_senders_for_source,
        merge_hdfc_cc_statement_sender_accounts,
        sender_emails_for_source_key,
    )

    bank: BankSendersConfig = {
        "alerts@hdfcbank.bank.in": {
            "parser_key": "hdfc_bank",
            "accounts": {
                "3703": {
                    "account_id": "HDFC_CC_3703",
                    "source_key": "hdfc_cc_3703",
                },
            },
            "expected_cadence": "per_transaction",
        },
    }
    merged = merge_hdfc_cc_statement_sender_accounts(bank, "hdfc_cc_3703")
    senders = sender_emails_for_source_key(merged, "hdfc_cc_3703")
    assert "emailstatements.cards@hdfcbank.net" in senders
    assert "emailstatements.cards@hdfcbank.bank.in" in senders
    stmt, alert = _partition_senders_for_source(merged, "hdfc_cc_3703")
    assert "emailstatements.cards@hdfcbank.net" in stmt
    assert "emailstatements.cards@hdfcbank.bank.in" in stmt
    assert "alerts@hdfcbank.bank.in" in alert


def test_collect_pending_queue_uses_gmail_subject_keywords_for_icici_direct() -> None:
    """Broker mailbox queries use subject:\"…\" clauses instead of bare ``from:`` only."""
    client = MagicMock()
    client.search_messages.return_value = []

    bank: BankSendersConfig = {
        "service@icicisecurities.com": {
            "parser_key": "icici_direct_statement",
            "expected_cadence": "quarterly",
            "accounts": {
                "0000": {
                    "account_id": "ICICI_DIRECT",
                    "source_key": "icici_direct_equity",
                },
            },
            "gmail_subject_filter_keywords": [
                "Equity Transaction Statement",
                "Mutual Fund Account Statement",
            ],
        },
    }
    with patch("scraper.onboarding_orchestrator._get_processed_ids", return_value=set()):
        _collect_pending_queue(
            client,
            bank,
            "icici_direct_equity",
            after=datetime.date(2020, 1, 1),
            before=datetime.date(2026, 1, 1),
            session=MagicMock(),
        )

    queries = [c[0][0] for c in client.search_messages.call_args_list]
    assert len(queries) == 2
    assert any('subject:"Equity Transaction Statement"' in q for q in queries)
    assert any('subject:"Mutual Fund Account Statement"' in q for q in queries)


def test_partition_statement_senders_annual_before_monthly() -> None:
    """Statement Gmail searches run in cadence order: annual before monthly."""
    from scraper.onboarding_orchestrator import _partition_senders_for_source

    bank: BankSendersConfig = {
        "estatement@x.com": {
            "accounts": {"1": {"source_key": "s", "account_id": "A"}},
            "expected_cadence": "monthly",
        },
        "annual@x.com": {
            "accounts": {"1": {"source_key": "s", "account_id": "A"}},
            "expected_cadence": "annual",
        },
    }
    stmt, alert = _partition_senders_for_source(bank, "s")
    assert stmt == ["annual@x.com", "estatement@x.com"]
    assert alert == []


def test_count_classification_unknowns_ignores_missing_spend_when_cp_cat_set(
    session: Session,
) -> None:
    """Queue is counterparty-focused: filled cp+cat leaves the count even if spend/upi are null."""
    session.add(
        Transaction(
            content_hash="h" + "y" * 60,
            txn_date=datetime.date(2024, 1, 2),
            account_id="HDFC_SAL_3703",
            user_id="u1",
            source_statement="hdfc_savings_test",
            source_type="email",
            direction="OUTFLOW",
            amount=2.0,
            raw_description="UPI SOME MERCHANT",
            txn_type="UPI_EXPENSE",
            channel="UPI",
            counterparty="Merchant",
            counterparty_category="Food",
            spend_category=None,
            upi_type=None,
            classification_source="RULES_USER",
        )
    )
    session.commit()
    assert count_classification_unknowns(session, user_id="u1", source_key="hdfc_savings_test") == 0


def test_llm_sensitive_category_queued_when_counterparty_fields_complete(
    session: Session,
) -> None:
    """LLM + Friends/Gifts/Misc with both labels set still appears for human review."""
    session.add(
        Transaction(
            content_hash="h" + "s" * 60,
            txn_date=datetime.date(2024, 3, 1),
            account_id="HDFC_SAL_3703",
            user_id="u1",
            source_statement="hdfc_savings_test",
            source_type="email",
            direction="OUTFLOW",
            amount=3.0,
            raw_description="UPI SOMEONE",
            txn_type="UPI_TRANSFER",
            channel="UPI",
            counterparty="Person A",
            counterparty_category="Friends and Family",
            classification_source="LLM",
        )
    )
    session.commit()
    assert count_classification_unknowns(session, user_id="u1", source_key="hdfc_savings_test") == 1


def test_rules_friends_not_queued_when_fully_classified(
    session: Session,
) -> None:
    """Rule-based Friends & Family is trusted — not duplicated in the review queue."""
    session.add(
        Transaction(
            content_hash="h" + "t" * 60,
            txn_date=datetime.date(2024, 3, 2),
            account_id="HDFC_SAL_3703",
            user_id="u1",
            source_statement="hdfc_savings_test",
            source_type="email",
            direction="OUTFLOW",
            amount=4.0,
            raw_description="UPI RULED",
            txn_type="UPI_TRANSFER",
            channel="UPI",
            counterparty="Person B",
            counterparty_category="Friends and Family",
            classification_source="RULES_USER",
        )
    )
    session.commit()
    assert count_classification_unknowns(session, user_id="u1", source_key="hdfc_savings_test") == 0


def test_llm_sensitive_skipped_when_user_already_reviewed_same_counterparty(
    session: Session,
) -> None:
    """After one USER_REVIEWED save for a name, new LLM sensitive rows for that label stay off-queue."""
    session.add(
        Transaction(
            content_hash="h" + "a" * 60,
            txn_date=datetime.date(2024, 4, 1),
            account_id="HDFC_SAL_3703",
            user_id="u1",
            source_statement="hdfc_savings_test",
            source_type="email",
            direction="OUTFLOW",
            amount=10.0,
            raw_description="UPI NASEEMA 1",
            txn_type="UPI_TRANSFER",
            channel="UPI",
            counterparty="Naseema Begum",
            counterparty_category="Gifts & Personal Transfers",
            classification_source="USER_REVIEWED",
        )
    )
    session.add(
        Transaction(
            content_hash="h" + "b" * 60,
            txn_date=datetime.date(2024, 4, 2),
            account_id="HDFC_SAL_3703",
            user_id="u1",
            source_statement="hdfc_savings_test",
            source_type="email",
            direction="OUTFLOW",
            amount=11.0,
            raw_description="UPI NASEEMA 2",
            txn_type="UPI_TRANSFER",
            channel="UPI",
            counterparty="  naseema begum ",
            counterparty_category="Friends and Family",
            classification_source="LLM",
        )
    )
    session.commit()
    assert count_classification_unknowns(session, user_id="u1", source_key="hdfc_savings_test") == 0


def test_llm_sensitive_still_queued_when_only_rules_row_for_same_counterparty(
    session: Session,
) -> None:
    """RULES_* on the same name does not count as human verification — LLM row still queues."""
    session.add(
        Transaction(
            content_hash="h" + "c" * 60,
            txn_date=datetime.date(2024, 4, 3),
            account_id="HDFC_SAL_3703",
            user_id="u1",
            source_statement="hdfc_savings_test",
            source_type="email",
            direction="OUTFLOW",
            amount=12.0,
            raw_description="UPI MERCHANT",
            txn_type="UPI_TRANSFER",
            channel="UPI",
            counterparty="Urban Company",
            counterparty_category="Gifts & Personal Transfers",
            classification_source="RULES_USER",
        )
    )
    session.add(
        Transaction(
            content_hash="h" + "d" * 60,
            txn_date=datetime.date(2024, 4, 4),
            account_id="HDFC_SAL_3703",
            user_id="u1",
            source_statement="hdfc_savings_test",
            source_type="email",
            direction="OUTFLOW",
            amount=13.0,
            raw_description="UPI URBAN",
            txn_type="UPI_TRANSFER",
            channel="UPI",
            counterparty="Urban Company",
            counterparty_category="Miscellaneous",
            classification_source="LLM",
        )
    )
    session.commit()
    assert count_classification_unknowns(session, user_id="u1", source_key="hdfc_savings_test") == 1


def test_pause_resume_state_helpers() -> None:
    s = {"status": "processing", "emails_processed": 5}
    p = pause_backfill_state(s)
    assert p["status"] == "paused"
    r = resume_backfill_state(p)
    assert r["status"] == "processing"
