"""
One-shot (or cron) backfill of holdings classification columns.

Fills:
  - Mutual funds: ``fund_category``, ``fund_house`` from AMFI NAVAll.txt
  - Equities / ESOP: ``sector`` from NSE, ``market_cap_class`` from a manual map

Prereq: API ``init_db()`` has run so SQLite patches added the columns (or fresh DB).

Usage:
  python3 scripts/enrich_holdings.py
  python3 scripts/enrich_holdings.py --user-id sashank
  python3 scripts/enrich_holdings.py --all-users   # user_id filter omitted
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlmodel import Session

from api.database import get_engine, init_db
from api.services.holding_enrichment import enrich_holdings


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich holdings from AMFI + NSE")
    parser.add_argument(
        "--user-id",
        default="sashank",
        help="Limit to this user_id (default: sashank)",
    )
    parser.add_argument(
        "--all-users",
        action="store_true",
        help="Do not filter by user — updates every active holding",
    )
    args = parser.parse_args()

    init_db()
    uid = None if args.all_users else (args.user_id.strip() or None)

    with Session(get_engine()) as session:
        report = enrich_holdings(session, user_id=uid, commit=True)
        print(json.dumps(report.as_dict(), indent=2))


if __name__ == "__main__":
    main()
