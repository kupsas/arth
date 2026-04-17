#!/usr/bin/env python3
"""
Deprecated — use ``scripts/scrape_historical.py --preset hdfc-cc-statement`` or
:func:`scraper.orchestrator.run_historical_backfill` with the same preset query.
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

import pipeline.config  # noqa: F401

from api.database import get_engine, init_db
from sqlmodel import Session

from scraper.orchestrator import HISTORICAL_GMAIL_QUERY_PRESETS, run_historical_backfill

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    logger.warning(
        "Deprecated: use scripts/scrape_historical.py --preset hdfc-cc-statement "
        "or run_historical_backfill(gmail_query=...)."
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--before",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
        default=dt.date.today(),
    )
    ap.add_argument(
        "--after",
        type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
        default=dt.date(2000, 1, 1),
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    init_db()
    with Session(get_engine()) as session:
        r = run_historical_backfill(
            session=session,
            after=args.after,
            before=args.before,
            gmail_query=HISTORICAL_GMAIL_QUERY_PRESETS["hdfc-cc-statement"],
            dry_run=args.dry_run,
        )
    print()
    print("=== Historical sweep (hdfc-cc-statement) ===")
    print(f"emails_found: {r.emails_found}")
    print(f"emails_processed: {r.emails_processed}")
    print(f"txns_created: {r.txns_created}")
    print(f"errors: {len(r.errors)}")


if __name__ == "__main__":
    main()
