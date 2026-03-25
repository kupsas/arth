#!/usr/bin/env python3
"""
Load roughly one calendar year of rows into ``prices`` for **current** market-priced holdings:

* **NSE-listed** sleeves (equity, ESOP, SGB, Indian gold ETF tickers) — official bhavcopy
  per session via :func:`api.services.price_feed.backfill_prices`.
* **Open-ended mutual funds** — historical NAV from the **AMFI portal** NAV history report
  (official), with **mfapi.in** only as a per-scheme fallback if a scheme has no rows.

**Database:** Set ``APP_ENV`` before running — same as the API (``test`` → ``data/arth_test.db``,
default ``prod`` → ``data/arth.db``).  See ``scripts/README.md`` for prerequisites and runbook.

Run from repo root::

    APP_ENV=test python3 scripts/backfill_price_history.py --days 365
    APP_ENV=test python3 scripts/backfill_price_history.py --days 365 --mf-only
    python3 scripts/backfill_price_history.py --days 365 --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_price_history")

# Space out mfapi.in fallback calls slightly (third-party; be polite).
_MFAPI_FALLBACK_SLEEP_SEC = 0.35
# Matches ``_NSE_BACKFILL_SLEEP_SEC`` in price_feed — gap between symbols after each symbol's day loop.
_NSE_SYMBOL_GAP_SEC = 0.35


def _weekdays_inclusive(start: datetime.date, end: datetime.date) -> int:
    """Count Mon–Fri days in ``[start, end]`` (NSE bhav attempts one file per such day)."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += datetime.timedelta(days=1)
    return n


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill prices (NSE bhav + MF AMFI portal history) for current portfolio symbols.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Approximate calendar depth to cover (~1y of trading sessions); default 365.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print symbol lists, date range, and estimated weekday count; no HTTP or DB writes.",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="If set, only holdings for this user_id are considered.",
    )
    parser.add_argument(
        "--buffer-days",
        type=int,
        default=14,
        help="Extra calendar days before --days window for NSE weekends/holidays (default 14).",
    )
    parser.add_argument(
        "--mf-only",
        action="store_true",
        help="Skip NSE bhav backfill; only load MF history (AMFI portal + mfapi fallback).",
    )
    args = parser.parse_args()

    # Imported after sys.path so `api` resolves; APP_ENV is read from the environment when
    # pipeline.config loads (set it in the shell before invoking this script).
    from sqlmodel import Session

    from api.database import get_engine
    from collections import Counter

    from api.services.mf_nav_history import (
        fetch_mf_nav_histories_amfi_portal,
        fetch_mf_nav_history_mfapi,
    )
    from api.services.price_feed import (
        backfill_prices,
        calendar_start_for_forced_nse_depth,
        latest_bhav_target_date,
        market_priced_holdings,
        mf_scheme_codes_for_holdings,
        nse_normalised_symbols_for_holdings,
        upsert_prices,
    )
    from pipeline.config import APP_ENV, DB_PATH

    engine = get_engine()
    target = latest_bhav_target_date()
    start = calendar_start_for_forced_nse_depth(
        target,
        depth_calendar_days=args.days,
        weekend_holiday_buffer_days=args.buffer_days,
    )

    with Session(engine) as session:
        holdings = market_priced_holdings(session, user_id=args.user_id)
        nse_syms = nse_normalised_symbols_for_holdings(holdings)
        mf_codes = mf_scheme_codes_for_holdings(holdings)

    logger.info("APP_ENV=%s DB_PATH=%s", APP_ENV, DB_PATH)
    if args.mf_only:
        logger.info("Mode: --mf-only (NSE bhav skipped)")
    logger.info("NSE latest session date (weekday anchor): %s", target)
    logger.info("Backfill inclusive range: %s .. %s", start, target)
    if not args.mf_only:
        logger.info(
            "Weekdays in range (~bhav downloads per NSE symbol): %d",
            _weekdays_inclusive(start, target),
        )
    logger.info("NSE symbols (%d): %s", len(nse_syms), ", ".join(nse_syms) or "(none)")
    logger.info("MF scheme codes (%d): %s", len(mf_codes), ", ".join(mf_codes) or "(none)")

    if args.dry_run:
        return 0

    with Session(engine) as session:
        if not args.mf_only:
            for i, sym in enumerate(nse_syms):
                logger.info("NSE [%d/%d] %s ...", i + 1, len(nse_syms), sym)
                res = backfill_prices(session, sym, start, target)
                logger.info("NSE [%d/%d] %s -> %s", i + 1, len(nse_syms), sym, res)
                if i < len(nse_syms) - 1:
                    time.sleep(_NSE_SYMBOL_GAP_SEC)

        if args.mf_only and not mf_codes:
            logger.warning("--mf-only but no MF scheme codes on holdings — nothing to write")

        if mf_codes:
            logger.info("MF: fetching AMFI portal history for %d scheme(s) ...", len(mf_codes))
            mf_rows = fetch_mf_nav_histories_amfi_portal(mf_codes, start, target)
            got = Counter(r.symbol for r in mf_rows)
            for j, code in enumerate(mf_codes):
                if got[code] == 0:
                    logger.warning(
                        "MF [%d/%d] scheme %s: no AMFI portal rows; trying mfapi.in",
                        j + 1,
                        len(mf_codes),
                        code,
                    )
                    extra = fetch_mf_nav_history_mfapi(code, start, target)
                    mf_rows.extend(extra)
                    logger.info(
                        "MF [%d/%d] scheme %s: mfapi.in -> %d row(s)",
                        j + 1,
                        len(mf_codes),
                        code,
                        len(extra),
                    )
                    if j < len(mf_codes) - 1:
                        time.sleep(_MFAPI_FALLBACK_SLEEP_SEC)
                else:
                    logger.info(
                        "MF [%d/%d] scheme %s: AMFI portal -> %d row(s)",
                        j + 1,
                        len(mf_codes),
                        code,
                        got[code],
                    )
            touched = upsert_prices(session, mf_rows) if mf_rows else 0
            logger.info("MF: upsert touched %d row(s) total (%d raw rows)", touched, len(mf_rows))

        session.commit()

    logger.info("Committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
