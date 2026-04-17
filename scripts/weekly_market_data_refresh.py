#!/usr/bin/env python3
"""
CLI entry for the same weekly refresh used by the API scheduler.

See :func:`api.services.weekly_market_refresh.run_weekly_market_data_refresh` for behaviour.

Examples::

  python3 scripts/weekly_market_data_refresh.py
  python3 scripts/weekly_market_data_refresh.py --user-id sashank
  python3 scripts/weekly_market_data_refresh.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.database import init_db
from api.services.weekly_market_refresh import run_weekly_market_data_refresh


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--user-id",
        default=None,
        metavar="USER",
        help="Limit price refresh + enrichment to this user (omit for all users)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned steps only (no DB writes, no network)",
    )
    args = parser.parse_args()
    uid = (args.user_id or "").strip() or None

    (REPO_ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "repo": str(REPO_ROOT),
                    "user_id_filter": uid,
                    "steps": [
                        "refresh_all_prices",
                        "refresh_nse_equity_reference",
                        "enrich_holdings",
                    ],
                    "note": "When the API server runs, the same sequence is scheduled weekly (see scraper.scheduler).",
                },
                indent=2,
            )
        )
        return 0

    init_db()
    summary = run_weekly_market_data_refresh(user_id=uid)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
