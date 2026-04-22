#!/usr/bin/env python3
"""
Remove rows from ``processed_emails`` so Gmail messages can be parsed again.

When a bank changes email copy, InstaAlerts are often recorded as **skipped**
(no parser matched) but the Gmail id stays in the ledger — historical backfill
and normal polling will *not* download them again. After you fix the parser,
delete those ledger rows for the affected window, then run::

    python3 scripts/scrape_historical.py --after 2026-04-15 --before 2026-04-30

**Safe default:** only deletes rows with ``status='skipped'`` (never ``processed``,
which would risk duplicate work if misused).

Examples::

  # Preview what would be removed (HDFC InstaAlerts, Apr 15–29 received)
  python3 scripts/clear_scraper_ledger_for_rescrape.py \\
      --after 2026-04-15 --before 2026-04-30 --dry-run

  # Actually delete skipped rows, then backfill
  python3 scripts/clear_scraper_ledger_for_rescrape.py \\
      --after 2026-04-15 --before 2026-04-30
  python3 scripts/scrape_historical.py --after 2026-04-15 --before 2026-04-30

  # Also retry failed messages in that window
  python3 scripts/clear_scraper_ledger_for_rescrape.py \\
      --after 2026-04-15 --before 2026-04-30 --include-failed

  # Only CC InstaAlerts (recommended after a parser fix — avoids re-pulling OTP / MAB noise)
  python3 scripts/clear_scraper_ledger_for_rescrape.py \\
      --after 2026-04-15 --before 2026-04-30 \\
      --subject-contains "A payment was made using your Credit Card"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401 — loads ``.env``

from sqlalchemy import delete, or_ as sa_or

from api.database import get_engine, init_db
from api.models import ProcessedEmail
from sqlmodel import Session, col, select

# Default: both InstaAlert From: addresses used in scraper.config.BANK_SENDERS
DEFAULT_HDFC_ALERT_SENDERS = (
    "alerts@hdfcbank.net",
    "alerts@hdfcbank.bank.in",
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--after",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
        required=True,
        help="Inclusive start date (matches received_at on the email).",
    )
    ap.add_argument(
        "--before",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
        required=True,
        help="Exclusive end date (same convention as scrape_historical.py).",
    )
    ap.add_argument(
        "--sender",
        action="append",
        dest="senders",
        metavar="EMAIL",
        help="Normalised sender to clear (repeatable). Default: both HDFC InstaAlert addresses.",
    )
    ap.add_argument(
        "--include-failed",
        action="store_true",
        help="Also delete rows with status='failed' (so backfill can retry).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching rows and counts only; do not delete.",
    )
    ap.add_argument(
        "--subject-contains",
        action="append",
        dest="subject_parts",
        metavar="TEXT",
        help="Only delete rows whose subject contains this substring (case-insensitive). "
        "Repeat to match if ANY phrase matches. "
        "When omitted, all skipped rows in the window for the sender(s) are cleared.",
    )
    args = ap.parse_args()

    if args.after >= args.before:
        ap.error("--after must be before --before (end is exclusive).")

    senders = [s.strip().lower() for s in args.senders] if args.senders else list(DEFAULT_HDFC_ALERT_SENDERS)
    # Naive datetimes — same convention as GET /api/scraper/emails date filters
    start = dt.datetime.combine(args.after, dt.time.min)
    end = dt.datetime.combine(args.before, dt.time.min)

    statuses: list[str] = ["skipped"]
    if args.include_failed:
        statuses.append("failed")

    init_db()
    with Session(get_engine()) as session:
        # Preview / count: ORM select (easier to print subjects)
        subject_filters = []
        if args.subject_parts:
            for part in args.subject_parts:
                p = (part or "").strip()
                if p:
                    subject_filters.append(col(ProcessedEmail.subject).ilike(f"%{p}%"))
        q = select(ProcessedEmail).where(
            col(ProcessedEmail.sender).in_(senders),
            col(ProcessedEmail.received_at) >= start,
            col(ProcessedEmail.received_at) < end,
            col(ProcessedEmail.status).in_(statuses),
        )
        if subject_filters:
            q = q.where(sa_or(*subject_filters))
        q = q.order_by(col(ProcessedEmail.received_at).desc())
        rows = list(session.exec(q).all())

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "would_delete": len(rows),
                        "after": str(args.after),
                        "before": str(args.before),
                        "senders": senders,
                        "statuses": statuses,
                        "subject_contains": args.subject_parts or None,
                        "sample": [
                            {
                                "gmail_message_id": r.gmail_message_id,
                                "sender": r.sender,
                                "subject": (r.subject or "")[:100],
                                "status": r.status,
                                "received_at": r.received_at.isoformat() if r.received_at else None,
                            }
                            for r in rows[:25]
                        ],
                    },
                    indent=2,
                )
            )
            if len(rows) > 25:
                print(f"# … {len(rows) - 25} more rows not shown", file=sys.stderr)
            return

        if not rows:
            print(json.dumps({"deleted": 0, "message": "No matching rows — nothing to do."}, indent=2))
            return

        ids = [r.id for r in rows if r.id is not None]
        if not ids:
            print(json.dumps({"error": "Selected rows had no primary keys — unexpected."}, indent=2))
            sys.exit(1)

        session.exec(delete(ProcessedEmail).where(col(ProcessedEmail.id).in_(ids)))
        session.commit()
        deleted = len(ids)

    print(
        json.dumps(
            {
                "deleted": deleted,
                "after": str(args.after),
                "before": str(args.before),
                "senders": senders,
                "statuses": statuses,
                "subject_contains": args.subject_parts or None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
