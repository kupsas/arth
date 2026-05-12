#!/usr/bin/env python3
"""
Copy rows from one SQLite ``prices`` table into another (upsert on symbol+date).

**Use case:** Run ``backfill_price_history.py`` against ``data/arth_test.db``, sanity-check
counts, then merge those rows into ``data/arth_main.db`` so NSE/mfapi are not hit again.

Uses :func:`api.services.price_feed.upsert_prices` so behavior matches the app and we do not
rely on SQLite UPSERT (older ``sqlite3`` builds lack ``ON CONFLICT ... DO UPDATE``).

This touches **only** the ``prices`` table — not holdings, transactions, or anything else.

Example::

    python3 scripts/merge_prices_from_db.py --source data/arth_test.db --into data/arth_main.db --dry-run
    python3 scripts/merge_prices_from_db.py --source data/arth_test.db --into data/arth_main.db

Paths are relative to the repo root (or absolute).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlmodel import Session, create_engine, select

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.models import Price  # noqa: E402
from api.services.price_feed import upsert_prices  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge prices rows from source DB into target DB.")
    parser.add_argument(
        "--source",
        type=Path,
        default=_ROOT / "data" / "arth_test.db",
        help="SQLite file to read prices from (default: data/arth_test.db).",
    )
    parser.add_argument(
        "--into",
        type=Path,
        default=_ROOT / "data" / "arth_main.db",
        help="SQLite file to write prices into (default: data/arth_main.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print how many rows would be read; no writes.",
    )
    args = parser.parse_args()

    src = args.source.resolve()
    dst = args.into.resolve()
    if not src.is_file():
        print(f"ERROR: source DB not found: {src}", file=sys.stderr)
        return 1
    if not dst.is_file():
        print(f"ERROR: target DB not found: {dst}", file=sys.stderr)
        return 1

    src_engine = create_engine(
        f"sqlite:///{src}",
        connect_args={"check_same_thread": False},
    )
    dst_engine = create_engine(
        f"sqlite:///{dst}",
        connect_args={"check_same_thread": False},
    )

    with Session(src_engine) as s_src:
        rows = list(s_src.exec(select(Price)).all())

    if args.dry_run:
        print(f"dry-run: source has {len(rows)} price rows; would upsert into {dst}")
        return 0

    # Fresh ORM rows — avoid carrying source ``id`` into target PK space.
    payload = [
        Price(symbol=r.symbol, date=r.date, close_price=r.close_price, source=r.source)
        for r in rows
    ]

    with Session(dst_engine) as s_dst:
        n = upsert_prices(s_dst, payload)
        s_dst.commit()

    print(f"Upserted {n} price row(s) from {src} into {dst}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
