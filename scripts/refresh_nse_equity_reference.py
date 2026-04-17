#!/usr/bin/env python3
"""
Populate ``nse_equity_reference`` from NIFTY 100, NIFTY MIDCAP 150, and the latest equity bhav.

**Market cap:** only for ``instrument_kind=EQUITY``: NIFTY 100 → ``LARGE_CAP``;
NIFTY MIDCAP 150 → ``MID_CAP``; every other **equity-style** bhav row → ``SMALL_CAP``.
Bonds, SGBs, REITs, etc. get ``instrument_kind`` set and ``market_cap_class=NULL``.

**When to run:** After deploy (once), then occasionally (e.g. yearly when index
constituents change meaningfully). Same network requirements as price refresh.

Usage::

    python3 scripts/refresh_nse_equity_reference.py

Does not touch ``holdings`` — run ``python3 scripts/enrich_holdings.py`` afterward if you
want ``market_cap_class`` / ``sector`` copied onto existing rows from the new cache.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlmodel import Session, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import NseEquityReference  # noqa: E402
from api.services.nse_equity_reference import refresh_nse_equity_reference  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Build snapshot in a rolled-back transaction (no DB write)",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=0,
        metavar="N",
        help="After refresh, print JSON for first N rows (0 = skip)",
    )
    args = p.parse_args()

    init_db()
    engine = get_engine()
    with Session(engine) as session:
        if args.dry_run:
            try:
                stats = refresh_nse_equity_reference(session, commit=False)
            finally:
                session.rollback()
            print(json.dumps(stats, indent=2))
            print("dry_run: rolled back (no changes persisted)")
            return 0
        stats = refresh_nse_equity_reference(session, commit=True)
        print(json.dumps(stats, indent=2))
        n = int(args.sample)
        if n > 0:
            rows = list(session.exec(select(NseEquityReference).limit(n)).all())
            for r in rows:
                print(
                    json.dumps(
                        {
                            "symbol": r.symbol,
                            "instrument_kind": r.instrument_kind,
                            "market_cap_class": r.market_cap_class,
                            "industry": r.industry,
                            "company_name": r.company_name,
                        },
                        indent=2,
                    )
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
