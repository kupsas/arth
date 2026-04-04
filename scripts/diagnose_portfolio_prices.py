#!/usr/bin/env python3
"""
Diagnose NSE bhav vs ``prices`` for current portfolio equity / gold ETF symbols.

Run from repo root::

    python3 scripts/diagnose_portfolio_prices.py
    python3 scripts/diagnose_portfolio_prices.py --user-id sashank

Prints:
  - Preferred bhav session (``latest_bhav_target_date``) vs resolved session
    (``resolve_nse_bhav_session_date``) when today's file is not published yet.
  - Per-symbol close from bhav for that session.
  - Last few ``prices`` rows per symbol (staleness / ``nse_cached`` debugging).

Does not modify the database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlmodel import Session, col, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import Holding, Price  # noqa: E402
from api.services.price_feed import (  # noqa: E402
    latest_bhav_target_date,
    nse_normalised_symbols_for_holdings,
    resolve_nse_bhav_session_and_map,
)
from pipeline.models import AssetClass, ValuationMethod  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--user-id", default=None, help="Filter holdings (default: all users)")
    args = p.parse_args(argv)

    init_db()
    engine = get_engine()
    with Session(engine) as session:
        q = select(Holding).where(
            Holding.is_active == True,  # noqa: E712
            Holding.valuation_method == ValuationMethod.MARKET_PRICE.value,
        )
        if args.user_id and str(args.user_id).strip():
            q = q.where(Holding.user_id == str(args.user_id).strip())
        holdings = list(session.exec(q).all())

    nse_holdings = [
        h
        for h in holdings
        if h.asset_class
        in (
            AssetClass.EQUITY.value,
            AssetClass.ESOP.value,
            AssetClass.GOLD.value,
            AssetClass.SOVEREIGN_GOLD_BOND.value,
        )
    ]
    symbols = nse_normalised_symbols_for_holdings(nse_holdings)
    preferred = latest_bhav_target_date()
    session_d, full_map = resolve_nse_bhav_session_and_map(preferred)

    print(f"preferred_bhav_date={preferred}")
    print(f"resolved_bhav_session={session_d} bhav_map_loaded={full_map is not None}")
    if full_map and session_d < preferred:
        print(
            "(resolved < preferred: latest file not ready — refresh uses last published session)"
        )

    if not symbols:
        print("No NSE-listed market-priced holdings to check.")
        return 0

    print(f"\nBhav closes for session {session_d}:")
    for s in sorted(symbols):
        c = full_map.get(s) if full_map else None
        print(f"  {s}: {c if c is not None else 'MISSING in bhav'}")

    print("\nLast 3 ``prices`` rows per symbol:")
    with Session(engine) as session:
        for s in sorted(symbols):
            rows = list(
                session.exec(
                    select(Price)
                    .where(Price.symbol == s)
                    .order_by(col(Price.date).desc())
                    .limit(3)
                ).all()
            )
            print(f"  {s}:")
            if not rows:
                print("    (no rows)")
                continue
            for r in rows:
                print(f"    date={r.date} close={r.close_price} source={r.source}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
