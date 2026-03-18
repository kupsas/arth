"""
Scraper orchestrator — the main entry point for one email scrape cycle.

This module wires together all the pieces in order:

  Gmail API  →  email router  →  email parsers  →  transform pipeline  →  DB writer

The public function is ``scrape_new_emails()``.  It is called by:
  - The APScheduler (Step 7) every POLL_INTERVAL_MINUTES
  - The /api/scraper/trigger endpoint (Step 8) for manual one-shot runs

Flow for each email:
  1.  Subject-line filter  (find_parser — no body download yet)
  2.  Dedup check          (is this Gmail message ID already in processed_emails?)
  3.  Body download        (get_message_body — only happens if steps 1+2 pass)
  4.  HTML parse           (parser.parse → list[ParsedTransaction])
  5.  Transform            (ParsedTransaction → CanonicalTransaction)
  6.  Rules classify       (fill channel, txn_type, upi_type deterministically)
  7.  LLM classify         (fill counterparty, counterparty_category, remaining gaps)
  8.  DB write             (write_to_db with source_type='email', is_reviewed=False)
  9.  Record in DB         (ProcessedEmail row — status='processed'|'skipped'|'failed')
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from api.models import ProcessedEmail
from pipeline import config as pipeline_config
from pipeline.db_writer import write_to_db
from pipeline.llm_classifier import classify_llm
from pipeline.models import ParsedTransaction
from pipeline.rules_classifier import classify_rules
from pipeline.transformer import transform
from scraper.config import ALL_SENDERS, SCRAPER_LOOKBACK_DAYS
from scraper.email_router import _normalise_sender, find_parser
from scraper.gmail_client import GmailClient, GmailMessage

logger = logging.getLogger(__name__)


# ─── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScrapeResult:
    """Summary returned after one full scrape cycle.

    Useful for the scheduler to log outcomes and for the API status endpoint
    to expose "last run" information.
    """
    emails_found:     int = 0   # total emails fetched from Gmail (including already-done ones)
    emails_processed: int = 0   # emails that produced ≥1 transaction and were written to DB
    emails_skipped:   int = 0   # emails with no matching parser, or parser returned []
    emails_failed:    int = 0   # emails where an exception was raised during processing
    txns_created:     int = 0   # new Transaction rows inserted (excludes reconciled/deduped)
    errors: list[str] = field(default_factory=list)  # human-readable error messages

    @property
    def total_attempted(self) -> int:
        """Total emails we actually tried to process (excludes already-done ones)."""
        return self.emails_processed + self.emails_skipped + self.emails_failed


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _get_lookback_date(session: Session, sender: str) -> datetime.date:
    """Return the date to use as the Gmail 'after:' query cutoff for this sender.

    Strategy:
      - If we've processed emails from this sender before, return the date of
        the most recently received email minus 1 day (safety buffer — catches
        late-arriving emails and avoids off-by-one at day boundaries).
      - If this is the very first run for this sender, fall back to
        SCRAPER_LOOKBACK_DAYS days ago.

    The 'after:' Gmail query is NOT the dedup mechanism — the processed_emails
    table is.  The query just keeps our window size reasonable.  It's OK if
    this date overlaps with already-processed emails; they'll be filtered by
    _get_processed_ids() before any body downloads happen.
    """
    # We query by normalised sender (no display name) because that's how we store it.
    latest = session.exec(
        select(ProcessedEmail)
        .where(ProcessedEmail.sender == sender)
        .order_by(ProcessedEmail.received_at.desc())  # type: ignore[attr-defined]
    ).first()

    if latest is not None:
        # Subtract 1 day to catch any email from the same day we last processed.
        return (latest.received_at - datetime.timedelta(days=1)).date()

    # First run — look back a fixed window to bootstrap the history.
    return datetime.date.today() - datetime.timedelta(days=SCRAPER_LOOKBACK_DAYS)


def _get_processed_ids(session: Session) -> set[str]:
    """Return the set of all Gmail message IDs we have ever processed.

    We load all IDs in one query rather than filtering per-sender, because:
      a) Gmail message IDs are globally unique — no cross-sender collisions.
      b) The processed_emails table stays small (a few thousand rows at most
         for a personal tool, even over years of operation).
      c) A single set lookup is O(1) and avoids repeated queries in the inner loop.
    """
    rows = session.exec(select(ProcessedEmail.gmail_message_id)).all()
    return set(rows)


def _record_email(
    session: Session,
    msg: GmailMessage,
    *,
    sender: str,
    status: str,
    txn_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Insert a ProcessedEmail row marking this message as handled.

    Args:
        session:       Open DB session.
        msg:           The GmailMessage we just handled.
        sender:        The *normalised* sender address (no display name).
                       We store this instead of msg.sender so lookups in
                       _get_lookback_date() work correctly.
        status:        "processed" | "skipped" | "failed"
        txn_count:     How many transactions were created (0 for skipped/failed).
        error_message: Exception message if status="failed".
    """
    pe = ProcessedEmail(
        gmail_message_id=msg.id,
        sender=sender,
        subject=msg.subject,
        received_at=msg.received_at,
        txn_count=txn_count,
        status=status,
        error_message=error_message,
    )
    session.add(pe)
    session.commit()


def _process_email(
    msg: GmailMessage,
    *,
    client: GmailClient,
    session: Session,
) -> tuple[str, int]:
    """Process one email through the full pipeline.

    Returns:
        ("skipped", 0)           — no parser matched, or parser returned []
        ("processed", txn_count) — txn_count ≥ 1 transactions were created

    Raises:
        Exception — any error during body download, parsing, or DB write.
        The caller is responsible for catching and recording the failure.
    """
    # ── Step 1: subject-line filter ──────────────────────────────────────────
    # find_parser() does a cheap string-match on subject.  No body download yet.
    parser = find_parser(msg.sender, msg.subject)

    if parser is None:
        # Sender is registered but no parser matched this subject.
        # This is normal — banks send non-transaction emails (MAB reminders,
        # marketing, OTP, etc.) from the same address as transaction alerts.
        logger.debug(
            "No parser for sender='%s' subject='%s' — skipping",
            msg.sender, msg.subject[:80],
        )
        return "skipped", 0

    # ── Step 2: download HTML body ───────────────────────────────────────────
    # We only download the body now that we know there's a parser for it.
    # This avoids paying the API cost for non-transaction emails.
    html_body = client.get_message_body(msg.id)

    # ── Step 3: parse HTML → ParsedTransaction list ──────────────────────────
    parsed_txns: list[ParsedTransaction] = parser.parse(
        html_body, msg.received_at.date()
    )

    if not parsed_txns:
        # Parser matched the subject but found no transaction in the body.
        # Examples: E-mandate email (has merchant name but no amount),
        # card-settings change notification, OTP emails.
        logger.debug(
            "Parser %s returned [] for subject='%s' — skipping",
            type(parser).__name__, msg.subject[:80],
        )
        return "skipped", 0

    # ── Step 4: group ParsedTransactions by (account_id, source_key) ─────────
    # One email normally produces transactions for exactly one account, but
    # grouping defensively here handles any edge cases cleanly.
    groups: dict[tuple[str, str], list[ParsedTransaction]] = {}
    for pt in parsed_txns:
        account_id = pt.metadata.get("account_id", "UNKNOWN")
        source_key = pt.metadata.get("source_key", "unknown")
        groups.setdefault((account_id, source_key), []).append(pt)

    total_new = 0
    for (account_id, source_key), group in groups.items():

        # ── Step 5: transform → CanonicalTransaction ─────────────────────────
        canonical = transform(
            group,
            account_id=account_id,
            currency="INR",
            source_statement=source_key,   # e.g. "hdfc_savings", "hdfc_cc_1905"
        )

        # ── Step 6: rules classifier ─────────────────────────────────────────
        # Fills channel, txn_type, upi_type deterministically from narration patterns.
        classify_rules(canonical)

        # ── Step 7: LLM classifier ───────────────────────────────────────────
        # Fills counterparty, counterparty_category, and any remaining gaps.
        classify_llm(canonical)

        # ── Step 8: write to DB ──────────────────────────────────────────────
        # source_type="email" means:
        #   - is_reviewed=False (transaction surfaces in the Review Queue)
        #   - gmail_message_id is stamped on each row for audit trail
        #   - reconciliation logic is SKIPPED for this write (we ARE the email row,
        #     not the statement row — the statement pipeline will reconcile against us)
        run = write_to_db(
            canonical,
            source_key=source_key,
            llm_model=pipeline_config.LLM_MODEL,
            session=session,
            source_type="email",
            gmail_message_id=msg.id,
        )
        total_new += run.new_count
        logger.debug(
            "    %s: %d new / %d backfilled / %d total canonical rows",
            account_id, run.new_count, run.updated_count, run.txn_count,
        )

    return "processed", total_new


# ─── Public entry point ────────────────────────────────────────────────────────

def scrape_new_emails(
    *,
    session: Session,
    client: GmailClient | None = None,
) -> ScrapeResult:
    """Run one full scrape cycle: fetch → parse → classify → write.

    This is the function that both the APScheduler and the manual trigger
    endpoint call.  It is intentionally synchronous — APScheduler runs it
    in a thread pool, so the FastAPI event loop is never blocked.

    Args:
        session: An open SQLModel Session.  The caller owns the lifecycle
                 (open before calling, close after).  Using the caller's
                 session means the entire scrape cycle participates in the
                 same DB context, making it easier to roll back if needed.
        client:  An authenticated GmailClient.  If None, a new one is created
                 and authenticated here.  Pass an explicit client to:
                   - Reuse one authenticated client across multiple cycles
                     (the scheduler does this)
                   - Inject a mock in tests

    Returns:
        ScrapeResult with counts for found / processed / skipped / failed emails
        and total transactions created.
    """
    result = ScrapeResult()

    # ── Authenticate if no client was supplied ────────────────────────────────
    if client is None:
        client = GmailClient()
        client.authenticate()

    # ── Load all already-processed message IDs upfront ───────────────────────
    # One query here instead of one per email in the inner loop.
    already_done = _get_processed_ids(session)

    # ── Iterate over each configured bank sender ──────────────────────────────
    for raw_sender in ALL_SENDERS:
        sender = _normalise_sender(raw_sender)  # strips display name, lowercases
        logger.info("── Scraping sender: %s", sender)

        # Determine the lookback window (first run → SCRAPER_LOOKBACK_DAYS days ago;
        # subsequent runs → 1 day before the most recently received email).
        after_date = _get_lookback_date(session, sender)
        logger.info("   Querying emails since %s", after_date)

        # ── Fetch email metadata from Gmail ───────────────────────────────────
        # This fetches subjects + IDs but NOT bodies — we download bodies only
        # for emails that pass the dedup check and subject filter.
        try:
            messages = client.fetch_emails(raw_sender, after_date=after_date)
        except Exception as exc:
            err = f"Gmail API error for {sender}: {exc}"
            logger.error(err)
            result.errors.append(err)
            continue  # move to the next sender; don't abort the whole cycle

        result.emails_found += len(messages)

        # ── Filter out already-processed emails ───────────────────────────────
        new_messages = [m for m in messages if m.id not in already_done]
        logger.info(
            "   %d total, %d already processed, %d new to process",
            len(messages), len(messages) - len(new_messages), len(new_messages),
        )

        # ── Process each new email ────────────────────────────────────────────
        for msg in new_messages:
            try:
                status, txn_count = _process_email(msg, client=client, session=session)

                _record_email(session, msg, sender=sender, status=status, txn_count=txn_count)

                if status == "processed":
                    result.emails_processed += 1
                    result.txns_created += txn_count
                    logger.info(
                        "   ✓ Processed '%s' → %d txn(s)", msg.subject[:70], txn_count
                    )
                else:
                    result.emails_skipped += 1
                    logger.debug("   · Skipped '%s'", msg.subject[:70])

                # Add to our local dedup set so if the same message ID somehow
                # appears again in this cycle (shouldn't happen, but defensive),
                # we don't try to process it twice.
                already_done.add(msg.id)

            except Exception as exc:
                error_msg = f"[{msg.id}] {msg.subject[:60]}: {exc}"
                logger.exception("Failed to process email %s (%s)", msg.id, msg.subject[:60])
                result.emails_failed += 1
                result.errors.append(error_msg)

                # Best-effort: record the failure so this email isn't retried
                # indefinitely on every poll.  If this also fails, swallow it —
                # the next run will simply try this email again.
                try:
                    _record_email(
                        session, msg, sender=sender,
                        status="failed", error_message=str(exc),
                    )
                    already_done.add(msg.id)
                except Exception:
                    pass

    logger.info(
        "Scrape cycle complete — processed: %d, skipped: %d, failed: %d, new txns: %d",
        result.emails_processed,
        result.emails_skipped,
        result.emails_failed,
        result.txns_created,
    )
    return result
