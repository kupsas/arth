"""
Integration tests for the scraper orchestrator (scraper/orchestrator.py).

Strategy:
  - GmailClient is injected as a MagicMock — no real Gmail API calls.
  - In-memory SQLite (StaticPool) — no filesystem side-effects.
  - LLM is disabled by patching pipeline.config.LLM_MODEL = "none".
  - Real HTML fixture files are used for email bodies so we test the full
    parse → transform → classify → write pipeline end-to-end.

Four key scenarios:
  1. Transaction email  → ProcessedEmail(status='processed') + Transaction(source_type='email')
  2. Non-transaction email (no parser match)    → ProcessedEmail(status='skipped'), no Transaction
  3. Non-transaction email (parser returns [])  → ProcessedEmail(status='skipped'), no Transaction
  4. Exception during processing                → ProcessedEmail(status='failed'), error captured
  5. Already-processed message ID              → filtered before body download (not re-processed)
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from api.models import ProcessedEmail, Transaction
from scraper.gmail_client import GmailMessage
from scraper.orchestrator import scrape_new_emails

FIXTURES = Path(__file__).parent / "fixtures" / "email_samples"

# Sender addresses that appear in ALL_SENDERS (scraper/config.py)
HDFC_SENDER  = "alerts@hdfcbank.net"
ICICI_SENDER = "customernotification@icici.bank.in"


# ─── DB fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(name="engine")
def in_memory_engine():
    """Fresh in-memory SQLite, shared via StaticPool so all sessions see the same data."""
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
    with Session(engine) as s:
        yield s


# ─── Mock client helpers ──────────────────────────────────────────────────────

def _make_msg(
    *,
    msg_id: str = "test_msg_001",
    sender: str = HDFC_SENDER,
    subject: str = "❗  You have done a UPI txn. Check details!",
    received_at: datetime.datetime | None = None,
) -> GmailMessage:
    """Build a fake GmailMessage dataclass."""
    return GmailMessage(
        id=msg_id,
        thread_id="thread_" + msg_id,
        sender=sender,
        subject=subject,
        received_at=received_at or datetime.datetime(2026, 3, 15, 10, 0, 0),
    )


def _sender_client(
    target_sender: str,
    messages: list[GmailMessage],
    body: str = "",
) -> MagicMock:
    """
    Mock GmailClient that returns ``messages`` only when fetch_emails is called
    for ``target_sender``, and [] for all other senders.

    This prevents cross-contamination: if the orchestrator queries both HDFC and
    ICICI senders, only the intended sender returns a message.
    """
    client = MagicMock()

    def _fetch(sender, **kwargs):
        return messages if sender == target_sender else []

    client.fetch_emails.side_effect = _fetch
    client.get_message_body.return_value = body
    return client


# ─── Scenario 1: Transaction email processed successfully ─────────────────────

class TestTransactionEmailProcessed:
    """
    A valid transaction email should:
      - Create one ProcessedEmail row with status='processed'
      - Create one Transaction row with source_type='email', is_reviewed=False
      - Stamp gmail_message_id on the Transaction row
    """

    def test_processed_email_status_is_processed(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg()
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        pe = session.exec(select(ProcessedEmail)).first()
        assert pe is not None
        assert pe.status == "processed"

    def test_transaction_has_source_type_email(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg()
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        txn = session.exec(select(Transaction)).first()
        assert txn is not None
        assert txn.source_type == "email"

    def test_transaction_is_unreviewed(self, session):
        """Email-sourced transactions enter as unreviewed so they appear in the review queue."""
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg()
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        txn = session.exec(select(Transaction)).first()
        assert txn.is_reviewed is False

    def test_gmail_message_id_stamped_on_transaction(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg(msg_id="hdfc_upi_fixture_001")
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        txn = session.exec(select(Transaction)).first()
        assert txn.gmail_message_id == "hdfc_upi_fixture_001"

    def test_scrape_result_counts(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg()
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            result = scrape_new_emails(session=session, client=client)

        assert result.emails_processed == 1
        assert result.txns_created == 1
        assert result.emails_failed == 0

    def test_processed_email_txn_count_set(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg()
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        pe = session.exec(select(ProcessedEmail)).first()
        assert pe.txn_count == 1


# ─── Scenario 2: Non-transaction email — no parser matches subject ─────────────

class TestNoParserMatchSkipped:
    """
    Emails from a registered sender but with an unrecognised subject line
    should be recorded as 'skipped' without downloading the body.
    """

    def test_skipped_status_recorded(self, session):
        # Subject matches no HDFC parser's can_parse()
        msg = _make_msg(
            msg_id="hdfc_stmt_email",
            subject="HDFC Bank: Your account statement for February 2026",
        )
        # Body is irrelevant — it should never be fetched
        client = _sender_client(HDFC_SENDER, [msg], body="<html>irrelevant</html>")

        with patch("pipeline.config.LLM_MODEL", "none"):
            result = scrape_new_emails(session=session, client=client)

        pe = session.exec(select(ProcessedEmail)).first()
        assert pe is not None
        assert pe.status == "skipped"
        assert result.emails_skipped == 1

    def test_no_transaction_row_for_no_parser_match(self, session):
        msg = _make_msg(
            msg_id="hdfc_stmt_email_2",
            subject="HDFC Bank: Your account statement for February 2026",
        )
        client = _sender_client(HDFC_SENDER, [msg])

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        txns = session.exec(select(Transaction)).all()
        assert txns == []

    def test_body_not_downloaded_for_no_parser_match(self, session):
        """
        The orchestrator's subject filter must stop before downloading the body.
        get_message_body() should NOT be called for an unmatched subject.
        """
        msg = _make_msg(subject="HDFC Bank: Unrecognised subject line")
        client = _sender_client(HDFC_SENDER, [msg])

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        client.get_message_body.assert_not_called()


# ─── Scenario 3: Non-transaction email — parser matches but returns [] ──────────

class TestParserReturnsEmptySkipped:
    """
    E-mandate and card-settings emails have subjects that match
    HDFCAccountUpdateParser.can_parse(), but parse() returns [] because there
    is no transaction amount.  The orchestrator should record these as 'skipped'.
    """

    def test_emandate_email_recorded_as_skipped(self, session):
        html = (FIXTURES / "hdfc_upi_inbound_01.html").read_text()
        msg = _make_msg(
            msg_id="emandate_001",
            subject="Account update for your HDFC Bank A/c",
        )
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            result = scrape_new_emails(session=session, client=client)

        pe = session.exec(select(ProcessedEmail)).first()
        assert pe.status == "skipped"
        assert result.emails_skipped == 1

    def test_card_settings_email_recorded_as_skipped(self, session):
        html = (FIXTURES / "hdfc_upi_inbound_03.html").read_text()
        msg = _make_msg(
            msg_id="card_settings_001",
            subject="Account update for your HDFC Bank A/c",
        )
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        pe = session.exec(select(ProcessedEmail)).first()
        assert pe.status == "skipped"

    def test_no_transaction_row_for_empty_parse(self, session):
        html = (FIXTURES / "hdfc_upi_inbound_01.html").read_text()
        msg = _make_msg(
            msg_id="emandate_no_txn",
            subject="Account update for your HDFC Bank A/c",
        )
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        txns = session.exec(select(Transaction)).all()
        assert txns == []


# ─── Scenario 4: Exception during processing → status='failed' ───────────────

class TestFailedEmailHandling:
    """
    If an exception is raised at any point (body download, parsing, DB write),
    the orchestrator should:
      - Record a ProcessedEmail row with status='failed' and the error message
      - NOT create a Transaction row
      - Continue to the next email (don't abort the whole cycle)
      - Add the message ID to already_done so it's not retried immediately
    """

    def test_failed_status_recorded_on_exception(self, session):
        # Parser will match the subject, so it WILL try to download the body.
        # get_message_body raises an exception → triggers failure path.
        msg = _make_msg(msg_id="fail_msg_001")
        client = MagicMock()
        client.fetch_emails.side_effect = lambda sender, **kw: (
            [msg] if sender == HDFC_SENDER else []
        )
        client.get_message_body.side_effect = RuntimeError("Gmail API timeout")

        with patch("pipeline.config.LLM_MODEL", "none"):
            result = scrape_new_emails(session=session, client=client)

        pe = session.exec(select(ProcessedEmail)).first()
        assert pe is not None
        assert pe.status == "failed"

    def test_error_message_captured_in_processed_email(self, session):
        msg = _make_msg(msg_id="fail_msg_002")
        client = MagicMock()
        client.fetch_emails.side_effect = lambda sender, **kw: (
            [msg] if sender == HDFC_SENDER else []
        )
        client.get_message_body.side_effect = RuntimeError("connection refused")

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        pe = session.exec(select(ProcessedEmail)).first()
        assert pe.error_message is not None
        assert "connection refused" in pe.error_message

    def test_failed_email_increments_failed_count(self, session):
        msg = _make_msg(msg_id="fail_msg_003")
        client = MagicMock()
        client.fetch_emails.side_effect = lambda sender, **kw: (
            [msg] if sender == HDFC_SENDER else []
        )
        client.get_message_body.side_effect = RuntimeError("boom")

        with patch("pipeline.config.LLM_MODEL", "none"):
            result = scrape_new_emails(session=session, client=client)

        assert result.emails_failed == 1

    def test_failed_email_does_not_create_transaction(self, session):
        msg = _make_msg(msg_id="fail_msg_004")
        client = MagicMock()
        client.fetch_emails.side_effect = lambda sender, **kw: (
            [msg] if sender == HDFC_SENDER else []
        )
        client.get_message_body.side_effect = RuntimeError("network error")

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)

        txns = session.exec(select(Transaction)).all()
        assert txns == []


# ─── Scenario 5: Already-processed message ID → not re-downloaded ─────────────

class TestAlreadyProcessedDedup:
    """
    The orchestrator loads all processed Gmail message IDs at the start of each
    cycle.  Messages that are already in processed_emails must be filtered BEFORE
    get_message_body() is called — we must not pay the API cost twice.
    """

    def test_body_not_re_downloaded_on_second_run(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg(msg_id="already_done_001")
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            # First run: processes normally
            scrape_new_emails(session=session, client=client)

            call_count_after_first = client.get_message_body.call_count
            assert call_count_after_first == 1, "Body should be fetched exactly once"

            # Second run: same message ID is in processed_emails now
            scrape_new_emails(session=session, client=client)

        # call count must not increase — the second run filtered the message
        assert client.get_message_body.call_count == call_count_after_first

    def test_no_duplicate_transaction_on_second_run(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg(msg_id="already_done_002")
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)
            scrape_new_emails(session=session, client=client)

        txns = session.exec(select(Transaction)).all()
        assert len(txns) == 1, "Second run must not create a duplicate transaction row"

    def test_no_duplicate_processed_email_on_second_run(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg(msg_id="already_done_003")
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)
            result2 = scrape_new_emails(session=session, client=client)

        # The second run should see 0 new emails to process
        assert result2.total_attempted == 0

    def test_second_run_scrape_result_has_zero_new(self, session):
        html = (FIXTURES / "alerts_hdfcbank_net_01.html").read_text()
        msg = _make_msg(msg_id="already_done_004")
        client = _sender_client(HDFC_SENDER, [msg], body=html)

        with patch("pipeline.config.LLM_MODEL", "none"):
            scrape_new_emails(session=session, client=client)
            result2 = scrape_new_emails(session=session, client=client)

        assert result2.txns_created == 0
