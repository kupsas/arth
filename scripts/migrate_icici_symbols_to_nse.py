#!/usr/bin/env python3
"""
Normalize ``investment_transactions.symbol`` for **ICICI Direct** equity rows.

Legacy imports often stored ICICI broker short codes (``TATMOT``, ``HDFBAN``, …) in
``symbol``. This script rewrites them to NSE tickers using the same mapping as price
refresh: :func:`api.services.price_feed.canonical_nse_symbol` (built from
:data:`parsers.holdings.icici_direct_equity.ICICI_SHORT_TO_NSE` plus optional
``data/icici_nse_symbol_overrides.json``).

**Skipped:** ``account_platform != \"ICICI Direct\"`` (including ``ICICI Direct MF``) and
rows with ``symbol IS NULL``.

Examples::

    # Preview changes for the DB your env points at (see pipeline.config.DB_PATH)
    python3 scripts/migrate_icici_symbols_to_nse.py --db-path data/arth.db

    # Apply + backup copy next to the DB file
    python3 scripts/migrate_icici_symbols_to_nse.py --db-path data/arth.db --execute --backup-db

    # Onboarding copy
    python3 scripts/migrate_icici_symbols_to_nse.py --db-path data/arth_onboarding.db --execute --backup-db
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlmodel import Session, col, create_engine, select  # noqa: E402

from api.models import InvestmentTransaction  # noqa: E402
from api.services.price_feed import canonical_nse_symbol  # noqa: E402


_ICICI_DIRECT_EQ = "ICICI Direct"


def _engine_for_sqlite_file(db_path: Path):
    """SQLite engine for an arbitrary file path (same pragmas as api/database)."""
    uri = f"sqlite:///{db_path.resolve()}"
    eng = create_engine(
        uri,
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 60},
    )
    return eng


def run_migration(
    engine,
    *,
    execute: bool,
) -> tuple[int, list[tuple[int, str, str]]]:
    """Return (n_updated, list of (id, old, new)) for rows that would change or changed."""
    changes: list[tuple[int, str, str]] = []
    with Session(engine) as session:
        stmt = (
            select(InvestmentTransaction)
            .where(InvestmentTransaction.account_platform == _ICICI_DIRECT_EQ)
            .where(col(InvestmentTransaction.symbol).is_not(None))
        )
        rows = list(session.exec(stmt).all())
        for r in rows:
            old = (r.symbol or "").strip()
            if not old:
                continue
            new = canonical_nse_symbol(old)
            if new != old:
                changes.append((r.id or 0, old, new))
                if execute and r.id is not None:
                    r.symbol = new
                    session.add(r)
        if execute:
            session.commit()
    return len(changes), changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db-path",
        type=Path,
        required=True,
        help="SQLite database file (e.g. data/arth.db or data/arth_onboarding.db).",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Apply updates (default is dry-run: print planned changes only).",
    )
    ap.add_argument(
        "--backup-db",
        action="store_true",
        help="Before --execute, copy DB to <name>.bak-migrate-symbols-<timestamp> beside the file.",
    )
    args = ap.parse_args()

    db_path = args.db_path.expanduser().resolve()
    if not db_path.is_file():
        print(f"ERROR: database file not found: {db_path}", file=sys.stderr)
        return 1

    if args.execute and args.backup_db:
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bak = db_path.parent / f"{db_path.name}.bak-migrate-symbols-{ts}"
        shutil.copy2(db_path, bak)
        print(f"Backed up to {bak}\n")

    engine = _engine_for_sqlite_file(db_path)
    n, changes = run_migration(engine, execute=args.execute)

    print(f"Database: {db_path}")
    print(f"Rows with symbol change: {n}")
    for rid, old, new in sorted(changes, key=lambda x: x[0]):
        print(f"  id={rid}  {old!r}  ->  {new!r}")

    if not args.execute:
        print("\nDry-run only. Re-run with --execute to apply.")
    else:
        print(f"\nUpdated {n} row(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
