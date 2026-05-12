#!/usr/bin/env python3
"""
Load roughly one calendar year of rows into ``prices`` for the historical portfolio symbol universe:

* **NSE-listed** sleeves (equity, ESOP, SGB, Indian gold ETF tickers) — official bhavcopy
  per session via :func:`api.services.price_feed.backfill_prices`.
* **Open-ended mutual funds** — historical NAV from the **AMFI portal** NAV history report
  (official), with **mfapi.in** only as a per-scheme fallback if a scheme has no rows.
* Includes **historically traded** symbols from linked ``investment_transactions`` so
  fully sold positions can still be valued in old months. ``STOONE`` is excluded.

**Database:** Set ``APP_ENV`` before running — same as the API (``test`` → ``data/arth_test.db``,
default ``prod`` → ``data/arth_main.db``).  See ``scripts/README.md`` for prerequisites and runbook.

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


def _weekdays_inclusive(start: datetime.date, end: datetime.date) -> int:
    """Count Mon–Fri days in ``[start, end]`` (NSE bhav attempts one file per such day)."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += datetime.timedelta(days=1)
    return n


def _first_weekday_on_or_after(d: datetime.date) -> datetime.date:
    out = d
    while out.weekday() >= 5:
        out += datetime.timedelta(days=1)
    return out


def _filtered_nse_symbols(symbols: list[str], start_symbol: str | None) -> list[str]:
    if not start_symbol:
        return symbols
    want = start_symbol.strip().upper()
    try:
        idx = symbols.index(want)
    except ValueError:
        logger.warning("--start-symbol=%s not found in NSE symbol list; running full list", want)
        return symbols
    return symbols[idx:]


def _chunk_symbols(symbols: list[str], chunk_count: int, chunk_index: int) -> list[str]:
    if chunk_count <= 1:
        return symbols
    total = len(symbols)
    if total == 0:
        return []
    base = total // chunk_count
    extra = total % chunk_count
    if chunk_index < extra:
        start = chunk_index * (base + 1)
        end = start + base + 1
    else:
        start = extra * (base + 1) + (chunk_index - extra) * base
        end = start + base
    return symbols[start:end]


def _already_covered_symbols(
    session,
    symbols: list[str],
    *,
    start: datetime.date,
    target: datetime.date,
) -> set[str]:
    from api.models import Price
    from sqlalchemy import func
    from sqlmodel import col, select

    covered: set[str] = set()
    effective_start = _first_weekday_on_or_after(start)
    for sym in symbols:
        mn, mx = session.exec(
            select(func.min(Price.date), func.max(Price.date)).where(Price.symbol == sym)
        ).one()
        if mn is not None and mx is not None and mn <= effective_start and mx >= target:
            covered.add(sym)
    return covered


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
    parser.add_argument(
        "--start-symbol",
        default=None,
        help="Resume NSE backfill from this canonical symbol (inclusive), e.g. BAJAJFINSV.",
    )
    parser.add_argument(
        "--chunk-count",
        type=int,
        default=1,
        help="Split NSE symbol list into this many contiguous chunks (default 1).",
    )
    parser.add_argument(
        "--chunk-index",
        type=int,
        default=0,
        help="0-based chunk index to run when using --chunk-count.",
    )
    parser.add_argument(
        "--skip-completed",
        action="store_true",
        help="Skip NSE symbols that already have prices covering the full requested range.",
    )
    args = parser.parse_args()
    if args.chunk_count < 1:
        parser.error("--chunk-count must be >= 1")
    if args.chunk_index < 0 or args.chunk_index >= args.chunk_count:
        parser.error("--chunk-index must be between 0 and chunk-count-1")

    # Imported after sys.path so `api` resolves; APP_ENV is read from the environment when
    # pipeline.config loads (set it in the shell before invoking this script).
    from sqlalchemy import func
    from sqlmodel import Session, col, select

    from api.database import get_engine
    from collections import Counter

    from api.services.historical_portfolio import historical_price_symbol_universe
    from api.services.mf_nav_history import (
        fetch_mf_nav_histories_amfi_portal,
        fetch_mf_nav_history_mfapi,
    )
    from api.models import Price
    from api.services.price_feed import (
        calendar_start_for_forced_nse_depth,
        fetch_equity_closes_from_nse_bhav,
        latest_bhav_target_date,
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
        uid = str(args.user_id).strip() if args.user_id is not None else "sashank"
        universe = historical_price_symbol_universe(session, user_id=uid)
        nse_syms = universe["nse_symbols"]
        mf_codes = universe["mf_codes"]
        unsupported_syms = universe["unsupported_symbols"]
        if args.skip_completed:
            covered = _already_covered_symbols(session, nse_syms, start=start, target=target)
            if covered:
                logger.info("Skipping already covered NSE symbols (%d): %s", len(covered), ", ".join(sorted(covered)))
            nse_syms = [sym for sym in nse_syms if sym not in covered]
    nse_syms = _filtered_nse_symbols(nse_syms, args.start_symbol)
    nse_syms = _chunk_symbols(nse_syms, args.chunk_count, args.chunk_index)
    if args.chunk_count > 1 and args.chunk_index != 0 and not args.mf_only:
        mf_codes = []

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
    if args.chunk_count > 1:
        logger.info("Running NSE chunk %d/%d", args.chunk_index + 1, args.chunk_count)
    logger.info("MF scheme codes (%d): %s", len(mf_codes), ", ".join(mf_codes) or "(none)")
    if unsupported_syms:
        logger.warning(
            "Symbols with no deep backfill path in this script (%d): %s",
            len(unsupported_syms),
            ", ".join(unsupported_syms),
        )

    if args.dry_run:
        return 0

    with Session(engine) as session:
        if not args.mf_only:
            total_weekdays = _weekdays_inclusive(start, target)
            for i, sym in enumerate(nse_syms):
                logger.info(
                    "NSE [%d/%d] %s ...",
                    i + 1,
                    len(nse_syms),
                    sym,
                )
                processed = 0
                hits = 0
                misses = 0
                buffered_rows: list[Price] = []
                d = start
                while d <= target:
                    if d.weekday() < 5:
                        processed += 1
                        px_map = fetch_equity_closes_from_nse_bhav([sym], d)
                        if sym in px_map:
                            hits += 1
                            buffered_rows.append(
                                Price(symbol=sym, date=d, close_price=px_map[sym], source="nse")
                            )
                        else:
                            misses += 1
                        if buffered_rows and (len(buffered_rows) >= 25 or d == target):
                            upsert_prices(session, buffered_rows)
                            session.commit()
                            buffered_rows.clear()
                        if processed == 1 or processed % 10 == 0 or d == target:
                            pct = (100.0 * processed / total_weekdays) if total_weekdays > 0 else 100.0
                            logger.info(
                                "NSE [%d/%d] %s %d/%d weekdays (%.1f%%) date=%s hits=%d misses=%d",
                                i + 1,
                                len(nse_syms),
                                sym,
                                processed,
                                total_weekdays,
                                pct,
                                d.isoformat(),
                                hits,
                                misses,
                            )
                        time.sleep(0.35)
                    d += datetime.timedelta(days=1)
                inserted = hits
                status = "ok" if hits > 0 else "no_data"
                res = {"symbol": sym, "inserted": inserted, "status": status}
                logger.info("NSE [%d/%d] %s -> %s", i + 1, len(nse_syms), sym, res)

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
