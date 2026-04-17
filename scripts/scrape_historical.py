#!/usr/bin/env python3
"""
Historical Gmail import — same pipeline as live scraping (DESKTOP_PREREQS item 7).

Wraps :func:`scraper.orchestrator.run_historical_backfill` so you can sweep a date
range without the API server. Uses ``processed_emails`` for message-id dedup.

Examples::

    # All configured bank senders between two dates (same as POST /api/scraper/backfill)
    python3 scripts/scrape_historical.py --after 2020-01-01 --before 2025-01-01

    # HDFC combined savings statement PDFs only (preset)
    python3 scripts/scrape_historical.py --preset hdfc-combined-statement --after 2020-01-01 --before 2026-01-01

    # Custom Gmail query (subject filters, etc.) — date bounds appended automatically
    python3 scripts/scrape_historical.py --query 'subject:"HDFC Bank Combined Email Statement"' \\
        --after 2020-01-01 --before 2025-01-01 --dry-run

Presets are defined in :data:`scraper.orchestrator.HISTORICAL_GMAIL_QUERY_PRESETS`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401 — loads ``.env``

from api.database import get_engine, init_db
from sqlmodel import Session

from scraper.orchestrator import (
    HISTORICAL_GMAIL_QUERY_PRESETS,
    run_historical_backfill,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--after", type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(), required=True)
    ap.add_argument(
        "--before",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
        default=dt.date.today(),
        help="Exclusive end date (default: today).",
    )
    ap.add_argument(
        "--preset",
        choices=sorted(HISTORICAL_GMAIL_QUERY_PRESETS.keys()),
        help="Use a built-in Gmail query (subject/sender filters).",
    )
    ap.add_argument(
        "--query",
        help="Full Gmail query fragment (date filters added automatically). Overrides --preset.",
    )
    ap.add_argument(
        "--sender",
        action="append",
        dest="senders",
        metavar="EMAIL",
        help="Restrict per-sender mode to this From: address (repeatable). Ignored with --query/--preset.",
    )
    ap.add_argument("--max-messages", type=int, default=None, help="Cap total messages (safety valve).")
    ap.add_argument("--dry-run", action="store_true", help="Count only; no parse or DB writes.")
    ap.add_argument(
        "--user-id",
        default=None,
        help="ARTH_SCRAPER_USER_ID / session user for secrets (default: env or sashank).",
    )
    args = ap.parse_args()

    gmail_query: str | None = None
    if args.query:
        gmail_query = args.query.strip()
    elif args.preset:
        gmail_query = HISTORICAL_GMAIL_QUERY_PRESETS[args.preset]

    sender_list = list(args.senders) if args.senders else None
    if gmail_query and sender_list:
        logger.warning("Ignoring --sender when using --query or --preset (custom query mode).")
        sender_list = None

    init_db()
    with Session(get_engine()) as session:
        result = run_historical_backfill(
            session=session,
            after=args.after,
            before=args.before,
            user_id=args.user_id,
            sender_emails=sender_list,
            gmail_query=gmail_query,
            max_messages=args.max_messages,
            dry_run=args.dry_run,
        )

    out = {
        "emails_found": result.emails_found,
        "emails_processed": result.emails_processed,
        "emails_skipped": result.emails_skipped,
        "emails_failed": result.emails_failed,
        "txns_created": result.txns_created,
        "errors": result.errors,
    }
    print(json.dumps(out, indent=2))
    if result.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
