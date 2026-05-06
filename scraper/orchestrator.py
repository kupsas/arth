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
import os
from dataclasses import dataclass, field
from typing import Protocol, cast

from sqlmodel import Session, select

from api.models import ProcessedEmail
from pipeline import config as pipeline_config
from pipeline.db_writer import write_to_db
from pipeline.holding_pipeline import ingest_holdings, ingest_investment_transactions
from pipeline.llm_classifier import classify_llm, import_flow_llm_status
from pipeline.models import ParsedTransaction
from pipeline.rules_classifier import classify_rules
from pipeline.transformer import transform
from scraper.config import BANK_SENDERS, SCRAPER_LOOKBACK_DAYS
from scraper.config_loader import all_sender_emails, get_bank_senders_config
from parsers.alerts.base import BaseEmailParser
from parsers.email_registry import build_email_parser_registry
from scraper.email_router import _normalise_sender, find_parser
from scraper.gmail_client import GmailClient, GmailMessage
from scraper.secrets_context import statement_secrets_context

from api.services.classifier_runtime import user_classifier_runtime
from api.services.email_import_flow_log import EmailImportFlowLog

logger = logging.getLogger(__name__)

# Compound Gmail queries (subject filters) for historical sweeps — used by
# ``run_historical_backfill(gmail_query=...)`` and ``scripts/scrape_historical.py``.
HISTORICAL_GMAIL_QUERY_PRESETS: dict[str, str] = {
    "hdfc-combined-statement": (
        "(from:hdfcbanksmartstatement@hdfcbank.net OR "
        "from:hdfcbanksmartstatement@hdfcbank.bank.in) "
        'subject:"HDFC Bank Combined Email Statement"'
    ),
    "hdfc-cc-statement": (
        "(from:emailstatements.cards@hdfcbank.net OR "
        "from:emailstatements.cards@hdfcbank.bank.in) "
        'subject:"Credit Card Statement"'
    ),
    "icici-nse-trades": (
        "(from:ebix@nse.co.in OR from:nseinvest@nse.co.in OR from:nse-direct@nse.co.in) "
        '"Trades executed at NSE"'
    ),
}


class _AttachmentEmailParser(Protocol):
    """Parsers with ``parse_type == "attachment"`` implement this (not :class:`BaseEmailParser` alone)."""

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str,
        email_subject: str,
    ) -> list[ParsedTransaction]: ...


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

def _get_lookback_date(
    session: Session,
    sender: str,
    bank_senders: dict | None = None,
) -> datetime.date:
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

    # First run — look back a fixed window to bootstrap the history.  Statement
    # senders (monthly PDFs) can override with ``first_run_lookback_days`` in
    # :data:`scraper.config.BANK_SENDERS` so the first poll is not shorter than a
    # typical billing cycle.
    days = SCRAPER_LOOKBACK_DAYS
    bs = bank_senders if bank_senders is not None else BANK_SENDERS
    for raw_addr, cfg in bs.items():
        if _normalise_sender(raw_addr) == sender:
            days = int(cfg.get("first_run_lookback_days", days))
            break

    return datetime.date.today() - datetime.timedelta(days=days)


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

    Idempotent: if a row with this gmail_message_id already exists, the call
    is silently skipped so that retried/resumed backfill chunks don't crash.

    Uses a SELECT guard **plus** a catch on IntegrityError so that concurrent
    requests processing the same message (race window between SELECT and INSERT)
    do not blow up.  The rollback on IntegrityError resets the session so the
    caller can keep using it for subsequent emails.
    """
    existing = session.exec(
        select(ProcessedEmail).where(
            ProcessedEmail.gmail_message_id == msg.id
        )
    ).first()
    if existing is not None:
        logger.debug("Skipping _record_email for %s — already in ledger", msg.id)
        return

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
    try:
        session.commit()
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc) or "IntegrityError" in type(exc).__name__:
            logger.debug(
                "Concurrent insert for processed_emails %s — rolling back duplicate (harmless race)",
                msg.id,
            )
            session.rollback()
        else:
            raise


def _process_email(
    msg: GmailMessage,
    *,
    client: GmailClient,
    session: Session,
    parser_registry: dict[str, list[BaseEmailParser]],
    user_id: str,
    import_flow_log: EmailImportFlowLog | None = None,
) -> tuple[str, int]:
    """Process one email through the full pipeline.

    Returns:
        ("skipped", 0)           — no parser matched, or parser returned []
        ("processed", txn_count) — txn_count ≥ 1 transactions were created

    Raises:
        Exception — any error during body download, parsing, or DB write.
        The caller is responsible for catching and recording the failure.
    """
    if import_flow_log:
        import_flow_log.write(
            "gmail_message_read",
            f"id={msg.id} subject_len={len(msg.subject or '')}",
        )

    # ── Step 1: subject-line filter ──────────────────────────────────────────
    # find_parser() does a cheap string-match on subject.  No body download yet.
    parser = find_parser(msg.sender, msg.subject, registry=parser_registry)

    if parser is None:
        # Sender is registered but no parser matched this subject.
        # This is normal — banks send non-transaction emails (MAB reminders,
        # marketing, OTP, etc.) from the same address as transaction alerts.
        logger.debug(
            "No parser for sender='%s' (subject_len=%d) — skipping",
            msg.sender,
            len(msg.subject or ""),
        )
        if import_flow_log:
            import_flow_log.write(
                "rules_routing",
                "no email parser matched this subject — skipped (not a known bank alert template)",
            )
        return "skipped", 0

    # ── Step 2: download body or PDF attachments ─────────────────────────────
    # HTML parsers ("body"): one InstaAlert-style email → one HTML body.
    # Statement parsers ("attachment"): monthly PDF(s) → many transactions.
    parse_type = getattr(parser, "parse_type", "body")
    received_date = msg.received_at.date()

    attachment_holdings: list = []
    attachment_inv_txns: list = []

    with statement_secrets_context(session, user_id):
        if parse_type == "attachment":
            if import_flow_log:
                import_flow_log.write("email_body_fetch", "attachment mode — fetching PDF part(s)")
            attachments = client.get_attachments(msg.id)
            if not attachments:
                logger.debug(
                    "Parser %s matched subject but no PDF attachments in message %s — skipping",
                    type(parser).__name__,
                    msg.id,
                )
                if import_flow_log:
                    import_flow_log.write("parse", "no PDF attachments — skipped")
                return "skipped", 0
            # Multi-PDF emails: parsers may accumulate holdings / inv_txns per file — reset once per message.
            reset_fn = getattr(parser, "reset_attachment_outputs", None)
            if callable(reset_fn):
                reset_fn()
            parsed_txns: list[ParsedTransaction] = []
            stmt_parser = cast(_AttachmentEmailParser, parser)
            for _filename, pdf_bytes in attachments:
                parsed_txns.extend(
                    stmt_parser.parse_attachment(
                        pdf_bytes,
                        received_date,
                        email_sender=_normalise_sender(msg.sender),
                        email_subject=msg.subject or "",
                    )
                )
            # Call once after all PDFs — parsers accumulate PPF / trade legs in ``parse_attachment``;
            # calling :meth:`attachment_investment_outputs` inside the loop duplicated rows on multi-PDF emails.
            inv_fn = getattr(parser, "attachment_investment_outputs", None)
            if callable(inv_fn):
                h, t = inv_fn()
                attachment_holdings.extend(h)
                attachment_inv_txns.extend(t)
        else:
            if import_flow_log:
                import_flow_log.write("email_body_fetch", "HTML body mode — downloading message body")
            html_body = client.get_message_body(msg.id)
            parsed_txns = parser.parse(html_body, received_date)

    # ── Step 3: ParsedTransaction list ready (+ optional PPF / holdings from PDF) ─

    if (
        not parsed_txns
        and not attachment_inv_txns
        and not attachment_holdings
    ):
        logger.debug(
            "Parser %s returned no bank rows and no investment rows (subject_len=%d) — skipping",
            type(parser).__name__,
            len(msg.subject or ""),
        )
        if import_flow_log:
            import_flow_log.write("parse", "parser returned no transactions — skipped")
        return "skipped", 0

    if import_flow_log:
        import_flow_log.write(
            "parse",
            f"parser={type(parser).__name__} bank_rows={len(parsed_txns)} inv_hint={bool(attachment_inv_txns or attachment_holdings)}",
        )

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

        # Overlay per-user LLM keys from encrypted ``UserSecrets`` for this slice only
        # (see :func:`api.services.classifier_runtime.user_classifier_runtime`).
        with user_classifier_runtime(session, user_id):
            # ── Step 5: transform → CanonicalTransaction ─────────────────────────
            canonical = transform(
                group,
                account_id=account_id,
                currency="INR",
                source_statement=source_key,   # e.g. "hdfc_savings", "hdfc_cc_1905"
            )

            # ── Step 6: rules classifier ─────────────────────────────────────────
            # Fills channel, txn_type, upi_type deterministically from narration patterns.
            from api.services.user_classification import pipeline_config_for_account_owner

            ucfg = pipeline_config_for_account_owner(session, account_id)
            classify_rules(canonical, ucfg)
            if import_flow_log:
                import_flow_log.write(
                    "rules_classification",
                    f"account_id={account_id} source_key={source_key} rows={len(canonical)} (deterministic rules applied)",
                )

            # ── Step 7: LLM classifier ───────────────────────────────────────────
            # Fills counterparty, counterparty_category, and any remaining gaps.
            if import_flow_log:
                import_flow_log.write("llm_phase", import_flow_llm_status(canonical))
            classify_llm(canonical)
            if import_flow_log:
                import_flow_log.write("llm_phase", "classify_llm() finished for this group")

            # ── Step 8: write to DB ──────────────────────────────────────────────
            # source_type="email" means:
            #   - is_reviewed=False (transaction surfaces on Review)
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
            if import_flow_log:
                import_flow_log.write(
                    "db_write",
                    f"source_key={source_key} new_txns={run.new_count} updated={run.updated_count}",
                )
            logger.debug(
                "    %s: %d new / %d backfilled / %d total canonical rows",
                account_id, run.new_count, run.updated_count, run.txn_count,
            )

    # ── Annual ICICI PDF: PPF → holdings + investment_transactions ────────────
    if attachment_holdings or attachment_inv_txns:
        hr = ingest_holdings(session, attachment_holdings, user_id=user_id)
        tr = ingest_investment_transactions(
            session,
            attachment_inv_txns,
            user_id=user_id,
            source_type="email",
            gmail_message_id=msg.id,
            import_flow_log=import_flow_log,
        )
        total_new += int(hr.get("inserted", 0)) + int(tr.get("inserted", 0))
        if import_flow_log:
            import_flow_log.write(
                "investment_ingest",
                f"holdings_ins={hr.get('inserted')} holdings_upd={hr.get('updated')} "
                f"inv_ins={tr.get('inserted')} inv_skip_dup={tr.get('skipped_duplicate')} inv_err={tr.get('errors')}",
            )
        logger.debug(
            "    investment: holdings upsert=%s/%s inv_txns inserted=%s skipped_dup=%s",
            hr.get("inserted"),
            hr.get("updated"),
            tr.get("inserted"),
            tr.get("skipped_duplicate"),
        )

    if import_flow_log:
        import_flow_log.write("email_done", f"status=processed new_transactions≈{total_new}")

    return "processed", total_new


# ─── Public entry point ────────────────────────────────────────────────────────

def _default_scraper_user_id() -> str:
    from api.constants import DEFAULT_LOCAL_USER

    return (
        os.environ.get("ARTH_SCRAPER_USER_ID") or DEFAULT_LOCAL_USER
    ).strip() or DEFAULT_LOCAL_USER


def scrape_new_emails(
    *,
    session: Session,
    client: GmailClient | None = None,
    user_id: str | None = None,
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
    uid = (user_id or "").strip() or _default_scraper_user_id()
    bank = get_bank_senders_config(session, uid)
    parser_registry = build_email_parser_registry(bank)

    # ── Authenticate if no client was supplied ────────────────────────────────
    if client is None:
        client = GmailClient()
        client.authenticate()

    # ── Load all already-processed message IDs upfront ───────────────────────
    # One query here instead of one per email in the inner loop.
    already_done = _get_processed_ids(session)

    # ── Iterate over each configured bank sender ──────────────────────────────
    for raw_sender in all_sender_emails(bank):
        sender = _normalise_sender(raw_sender)  # strips display name, lowercases
        logger.debug("── Scraping sender: %s", sender)

        # Determine the lookback window (first run → SCRAPER_LOOKBACK_DAYS days ago;
        # subsequent runs → 1 day before the most recently received email).
        after_date = _get_lookback_date(session, sender, bank)
        logger.debug("   Querying emails since %s", after_date)

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
        logger.debug(
            "   %d total, %d already in scraper ledger (dedup), %d new to process",
            len(messages), len(messages) - len(new_messages), len(new_messages),
        )

        # ── Process each new email ────────────────────────────────────────────
        for msg in new_messages:
            try:
                status, txn_count = _process_email(
                    msg,
                    client=client,
                    session=session,
                    parser_registry=parser_registry,
                    user_id=uid,
                )

                _record_email(session, msg, sender=sender, status=status, txn_count=txn_count)

                if status == "processed":
                    result.emails_processed += 1
                    result.txns_created += txn_count
                    logger.debug(
                        "   ✓ Processed message %s → %d txn(s)",
                        msg.id,
                        txn_count,
                    )
                else:
                    result.emails_skipped += 1
                    logger.debug("   · Skipped message %s", msg.id)

                # Add to our local dedup set so if the same message ID somehow
                # appears again in this cycle (shouldn't happen, but defensive),
                # we don't try to process it twice.
                already_done.add(msg.id)

            except Exception as exc:
                error_msg = f"[{msg.id}] subject_len={len(msg.subject or '')}: {exc}"
                logger.exception("Failed to process email %s", msg.id)
                session.rollback()
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

    # One friendly summary line is logged by :mod:`scraper.scheduler` after each cycle.
    logger.debug(
        "Scrape cycle counts — processed=%d skipped=%d failed=%d txns=%d",
        result.emails_processed,
        result.emails_skipped,
        result.emails_failed,
        result.txns_created,
    )
    return result


def run_historical_backfill(
    *,
    session: Session,
    after: datetime.date,
    before: datetime.date,
    client: GmailClient | None = None,
    user_id: str | None = None,
    sender_emails: list[str] | None = None,
    gmail_query: str | None = None,
    max_messages: int | None = None,
    dry_run: bool = False,
) -> ScrapeResult:
    """Sweep Gmail between ``after`` (inclusive) and ``before`` (exclusive).

    Uses the same parse → classify → :func:`pipeline.db_writer.write_to_db` path as
    live scraping, with explicit date bounds instead of incremental lookback.
    ``processed_emails`` still dedupes message IDs.

    **Two modes:**

    - **Per-sender (default):** one ``from:sender after:… before:…`` query per address
      in config (or restrict with ``sender_emails``).
    - **Custom query:** pass ``gmail_query`` (e.g. subject filters for HDFC combined
      statements). Do not pass ``sender_emails`` together with ``gmail_query``.

    Preset strings live in :data:`HISTORICAL_GMAIL_QUERY_PRESETS`.
    """
    if gmail_query is not None and sender_emails is not None:
        raise ValueError("Use either gmail_query or sender_emails, not both.")

    result = ScrapeResult()
    uid = (user_id or "").strip() or _default_scraper_user_id()
    bank = get_bank_senders_config(session, uid)
    parser_registry = build_email_parser_registry(bank)
    senders = sender_emails if sender_emails else all_sender_emails(bank)
    after_s = after.strftime("%Y/%m/%d")
    before_s = before.strftime("%Y/%m/%d")

    if client is None:
        client = GmailClient()
        client.authenticate()

    already_done = _get_processed_ids(session)

    # ── Mode: arbitrary Gmail query (subject filters, OR from: clauses, etc.) ─
    if gmail_query is not None:
        full_query = f"{gmail_query.strip()} after:{after_s} before:{before_s}"
        logger.debug("Historical backfill (custom query): %s", full_query[:200])
        try:
            messages = client.search_messages(
                full_query,
                paginate=True,
                max_results_per_page=100,
                max_total=max_messages,
            )
        except Exception as exc:
            result.errors.append(f"Gmail API error: {exc}")
            logger.error("%s", result.errors[-1])
            return result

        result.emails_found += len(messages)
        new_messages = [m for m in messages if m.id not in already_done]
        logger.debug(
            "   %d total, %d already in scraper ledger (dedup), %d new to process",
            len(messages),
            len(messages) - len(new_messages),
            len(new_messages),
        )

        for msg in new_messages:
            sender_norm = _normalise_sender(msg.sender)
            if dry_run:
                result.emails_skipped += 1
                already_done.add(msg.id)
                continue
            try:
                status, txn_count = _process_email(
                    msg,
                    client=client,
                    session=session,
                    parser_registry=parser_registry,
                    user_id=uid,
                )
                _record_email(session, msg, sender=sender_norm, status=status, txn_count=txn_count)
                if status == "processed":
                    result.emails_processed += 1
                    result.txns_created += txn_count
                else:
                    result.emails_skipped += 1
                already_done.add(msg.id)
            except Exception as exc:
                session.rollback()
                result.emails_failed += 1
                result.errors.append(f"[{msg.id}] {exc}")
                try:
                    _record_email(
                        session,
                        msg,
                        sender=sender_norm,
                        status="failed",
                        error_message=str(exc),
                    )
                    already_done.add(msg.id)
                except Exception:
                    pass

        logger.info(
            "Historical import finished — %d transaction(s) added from your email archive.",
            result.txns_created,
        )
        logger.debug(
            "Historical backfill (custom query) counts — processed=%d skipped=%d failed=%d txns=%d",
            result.emails_processed,
            result.emails_skipped,
            result.emails_failed,
            result.txns_created,
        )
        return result

    for raw_sender in senders:
        sender_norm = _normalise_sender(raw_sender)
        query = f"from:{raw_sender} after:{after_s} before:{before_s}"
        logger.debug("Backfill query: %s", query)

        try:
            messages = client.search_messages(
                query,
                paginate=True,
                max_results_per_page=100,
                max_total=max_messages,
            )
        except Exception as exc:
            result.errors.append(f"Gmail API error for {sender_norm}: {exc}")
            logger.error("%s", result.errors[-1])
            continue

        result.emails_found += len(messages)
        new_messages = [m for m in messages if m.id not in already_done]
        logger.debug(
            "   %s — %d total, %d new to process",
            sender_norm,
            len(messages),
            len(new_messages),
        )

        for msg in new_messages:
            if dry_run:
                result.emails_skipped += 1
                already_done.add(msg.id)
                continue
            try:
                status, txn_count = _process_email(
                    msg,
                    client=client,
                    session=session,
                    parser_registry=parser_registry,
                    user_id=uid,
                )
                _record_email(session, msg, sender=sender_norm, status=status, txn_count=txn_count)
                if status == "processed":
                    result.emails_processed += 1
                    result.txns_created += txn_count
                else:
                    result.emails_skipped += 1
                already_done.add(msg.id)
            except Exception as exc:
                session.rollback()
                result.emails_failed += 1
                result.errors.append(f"[{msg.id}] {exc}")
                try:
                    _record_email(
                        session,
                        msg,
                        sender=sender_norm,
                        status="failed",
                        error_message=str(exc),
                    )
                    already_done.add(msg.id)
                except Exception:
                    pass

    logger.info(
        "Historical import finished — %d transaction(s) added from your email archive.",
        result.txns_created,
    )
    logger.debug(
        "Historical backfill (per-sender) counts — processed=%d skipped=%d failed=%d txns=%d",
        result.emails_processed,
        result.emails_skipped,
        result.emails_failed,
        result.txns_created,
    )
    return result
