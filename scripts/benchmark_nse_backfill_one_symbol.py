#!/usr/bin/env python3
"""
Benchmark one NSE symbol through the same code path as onboarding backfill.

Uses :func:`api.services.price_feed.backfill_prices` (NSE bhavcopy only).  Prints
wall-clock time, rows upserted, and how many ``prices`` rows exist for that
symbol in the requested date range after the run.

**Database:** Same resolution as the API (see ``pipeline.config.resolve_db_path``).
``APP_ENV`` defaults to **prod** → ``data/arth_main.db``. For benchmarks, prefer::

    APP_ENV=onboarding python3 scripts/benchmark_nse_backfill_one_symbol.py SYMBOL

Writes to ``data/arth_main.db`` are **blocked** unless you pass ``--allow-prod``.

Examples::

    # Default window: 2023-08-01 .. 2023-12-31 (Aug–Dec 2023)
    APP_ENV=onboarding python3 scripts/benchmark_nse_backfill_one_symbol.py RELIANCE

    APP_ENV=onboarding python3 scripts/benchmark_nse_backfill_one_symbol.py INFY \\
        --start 2023-08-01 --end 2023-12-31

    # Shows which DB would be used; no network / DB writes
    APP_ENV=onboarding python3 scripts/benchmark_nse_backfill_one_symbol.py INFY --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Loads ``.env`` and resolves ``DB_PATH`` (same as API).
from pipeline.config import APP_ENV, DB_PATH, REPO_ROOT

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("benchmark_nse_backfill_one_symbol")

# Canonical production file — writes refused unless ``--allow-prod``.
_PROD_SQLITE = (REPO_ROOT / "data" / "arth_main.db").resolve()


def _print_database_target() -> None:
    """Echo exactly which file SQLite will use (must match user expectation)."""
    print()
    print("--- database target ---")
    print(f"APP_ENV={APP_ENV!r}")
    arth_name = os.getenv("ARTH_DB_NAME", "").strip()
    arth_path = os.getenv("ARTH_DB_PATH", "").strip()
    if arth_name:
        print(f"ARTH_DB_NAME={arth_name!r}")
    if arth_path:
        print(f"ARTH_DB_PATH={arth_path!r}")
    resolved = DB_PATH.resolve()
    print(f"SQLite file (absolute): {resolved}")
    if resolved == _PROD_SQLITE:
        print(
            "WARNING: This is the production database (data/arth_main.db). "
            "Benchmark writes are blocked unless you pass --allow-prod.",
        )
    print("-----------------------")


def _weekdays_inclusive(start: datetime.date, end: datetime.date) -> int:
    """Mon–Fri days in [start, end] (NSE publishes bhav on these; holidays omitted)."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += datetime.timedelta(days=1)
    return n


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Time a single-symbol NSE bhav backfill over a fixed date range.",
    )
    parser.add_argument(
        "symbol",
        help="NSE ticker as stored in bhav (e.g. RELIANCE, INFY, BAJAJFINSV).",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2023-08-01",
        help="Inclusive start date (YYYY-MM-DD). Default: 2023-08-01.",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="2023-12-31",
        help="Inclusive end date (YYYY-MM-DD). Default: 2023-12-31.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print date range and weekday count only; no DB or HTTP.",
    )
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help=(
            "Allow writing to data/arth_main.db (production). Without this flag, the "
            "script exits if the resolved DB is the canonical prod file."
        ),
    )
    args = parser.parse_args()

    _print_database_target()

    # Block accidental prod writes before parsing dates / hitting the network.
    if not args.dry_run:
        resolved_db = DB_PATH.resolve()
        if resolved_db == _PROD_SQLITE and not args.allow_prod:
            print()
            print(
                "ERROR: Refusing to write to production SQLite "
                f"({_PROD_SQLITE}).\n"
                "  Set APP_ENV=onboarding (or APP_ENV=test), or pass --allow-prod "
                "only if you intentionally want to modify arth_main.db.",
            )
            return 2

    sym = args.symbol.strip().upper()
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    if start > end:
        parser.error("--start must be on or before --end")

    weekdays = _weekdays_inclusive(start, end)
    print(
        f"Symbol={sym}  range={start.isoformat()}..{end.isoformat()}  "
        f"calendar_days={(end - start).days + 1}  mon_fri_weekdays≈{weekdays}",
    )

    if args.dry_run:
        print("(dry-run — no backfill executed)")
        return 0

    from sqlalchemy import func
    from sqlmodel import select

    from api.database import SQLiteSerializingSession, get_engine
    from api.models import Price
    from api.services.price_feed import backfill_prices, canonical_nse_symbol

    engine = get_engine()

    t0 = time.perf_counter()
    with SQLiteSerializingSession(engine) as session:
        result = backfill_prices(session, sym, start, end)
        session.commit()
    elapsed = time.perf_counter() - t0

    inserted = result.get("inserted", 0)
    status = result.get("status", "?")
    canon_sym = str(result.get("symbol") or canonical_nse_symbol(sym))

    # Rows present for the canonical symbol in range (matches DB column after upsert).
    with SQLiteSerializingSession(engine) as session:
        n_in_range = session.exec(
            select(func.count(Price.id)).where(
                Price.symbol == canon_sym,
                Price.date >= start,
                Price.date <= end,
            )
        ).one()

    print()
    print("--- result ---")
    print(f"backfill_prices return: {result}")
    print(f"wall_clock_seconds: {elapsed:.3f}")
    print(f"status: {status}")
    print(f"rows_upserted_this_call (inserted): {inserted}")
    print(f"rows_in_prices_table_for_symbol_in_range: {n_in_range}")
    if inserted > 0:
        sec_per_row = elapsed / inserted
        print(f"seconds_per_row_upserted: {sec_per_row:.4f}")
    if weekdays > 0:
        print(f"seconds_per_calendar_weekday_in_range: {elapsed / weekdays:.4f}  (rough upper bound)")
    print()
    print(
        "Note: NSE backfill walks trading sessions; inserted rows ≈ trading days with "
        "a bhav file, not every Mon–Fri (holidays have no file).",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
