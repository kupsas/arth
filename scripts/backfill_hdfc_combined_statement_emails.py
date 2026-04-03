#!/usr/bin/env python3
"""
Backfill HDFC **combined email statement** PDFs (savings 3703) into ``transactions``.

Sender: ``hdfcbanksmartstatement@hdfcbank.…`` — subjects must contain
``Combined Email Statement`` (see :mod:`scraper.email_parsers.hdfc_statement`).

Uses the same orchestration as live scrape: parse → classify → ``write_to_db`` with
``source_type=email``.  Skips Gmail message ids already in ``processed_emails``.

Examples::

    python3 scripts/backfill_hdfc_combined_statement_emails.py --before 2025-01-01
    python3 scripts/backfill_hdfc_combined_statement_emails.py --before 2025-01-01 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401 — loads ``.env``

from api.database import get_engine, init_db
from sqlmodel import Session

from scraper.email_router import _normalise_sender
from scraper.gmail_client import GmailClient
from scraper.orchestrator import (
    _get_processed_ids,
    _process_email,
    _record_email,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_QUERY = (
    "(from:hdfcbanksmartstatement@hdfcbank.net "
    "OR from:hdfcbanksmartstatement@hdfcbank.bank.in) "
    'subject:"HDFC Bank Combined Email Statement"'
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--before",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
        default=dt.date.today(),
        help="Only emails received strictly before this date (default: today).",
    )
    ap.add_argument(
        "--after",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
        default=dt.date(2000, 1, 1),
        help="Only emails on or after this date (default: 2000-01-01).",
    )
    ap.add_argument(
        "--query",
        default=_DEFAULT_QUERY,
        help="Gmail search prefix; date filters are appended.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching messages and exit without parsing or DB writes.",
    )
    args = ap.parse_args()

    before_s = args.before.strftime("%Y/%m/%d")
    after_s = args.after.strftime("%Y/%m/%d")
    full_query = f"{args.query} after:{after_s} before:{before_s}"

    client = GmailClient()
    client.authenticate()

    messages = client.search_messages(
        full_query,
        paginate=True,
        max_results_per_page=100,
    )
    logger.info(
        "Gmail query: %s → %d message(s)",
        full_query[:120] + ("…" if len(full_query) > 120 else ""),
        len(messages),
    )

    if args.dry_run:
        for m in sorted(messages, key=lambda x: x.received_at):
            print(m.received_at.date(), m.id[:16], (m.subject or "")[:80])
        return

    init_db()
    processed = _get_processed_ids(Session(get_engine()))

    skipped_done = 0
    skipped_parser = 0
    failed = 0
    total_new_txns = 0
    processed_emails = 0

    with Session(get_engine()) as session:
        for msg in sorted(messages, key=lambda x: x.received_at):
            if msg.id in processed:
                skipped_done += 1
                continue

            sender = _normalise_sender(msg.sender)
            try:
                status, txn_count = _process_email(msg, client=client, session=session)
                _record_email(
                    session,
                    msg,
                    sender=sender,
                    status=status,
                    txn_count=txn_count,
                )
                processed.add(msg.id)
                if status == "processed":
                    processed_emails += 1
                    total_new_txns += txn_count
                else:
                    skipped_parser += 1
            except Exception as exc:
                failed += 1
                logger.exception("Failed %s: %s", msg.id, (msg.subject or "")[:60])
                try:
                    _record_email(
                        session,
                        msg,
                        sender=sender,
                        status="failed",
                        error_message=str(exc),
                    )
                    processed.add(msg.id)
                except Exception:
                    pass

    print()
    print("=== Backfill summary ===")
    print(f"Messages matching query:     {len(messages)}")
    print(f"Already in processed_emails: {skipped_done}")
    print(f"Newly processed (≥1 txn):    {processed_emails}")
    print(f"New transaction rows (sum):   {total_new_txns}")
    print(f"Recorded as skipped:         {skipped_parser}")
    print(f"Failed:                       {failed}")


if __name__ == "__main__":
    main()
