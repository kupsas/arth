#!/usr/bin/env python3
"""
Delete specific ``investment_transactions`` rows by primary key.

Use this when you have confirmed duplicates or mis-tagged rows (for example wrong
``symbol`` vs ``notes`` ISIN) and want to drop only those ids without touching the rest
of the ledger.

**Safety**

- Default is **dry-run**: prints what would be deleted. Pass ``--execute`` to apply.
- Optionally ``--backup-db`` copies the resolved SQLite file first (same idea as other repair scripts).

Examples (repo root, after ``.env`` / ``APP_ENV`` point at the DB you mean to edit)::

    # Preview rows 184 and 185
    python3 scripts/delete_investment_transactions_by_id.py --id 184 --id 185

    # Delete after checking the preview
    python3 scripts/delete_investment_transactions_by_id.py --id 184 --id 185 --execute

    # Safer: backup then delete
    python3 scripts/delete_investment_transactions_by_id.py --id 184 --id 185 --execute --backup-db
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401, E402 — resolves DB_PATH from .env

from sqlmodel import Session, col, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import InvestmentTransaction  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--id",
        type=int,
        action="append",
        dest="ids",
        metavar="PK",
        required=True,
        help="Primary key of investment_transactions row (repeat for multiple ids).",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually DELETE rows. Without this, only print what would be removed.",
    )
    ap.add_argument(
        "--backup-db",
        action="store_true",
        help=(
            f"Copy {pipeline.config.DB_PATH} to a timestamped "
            "``.bak-del-inv-*`` file beside it before delete."
        ),
    )
    args = ap.parse_args()

    want = sorted(set(args.ids))
    if not want:
        print("ERROR: pass at least one --id", file=sys.stderr)
        return 1

    init_db()
    engine = get_engine()

    with Session(engine) as session:
        rows = list(
            session.exec(
                select(InvestmentTransaction).where(col(InvestmentTransaction.id).in_(want))
            ).all()
        )
    by_id = {r.id: r for r in rows if r.id is not None}
    missing = [i for i in want if i not in by_id]

    print(f"Database: {pipeline.config.DB_PATH}")
    print(f"Requested ids: {want}")
    if missing:
        print(f"WARNING: no row found for id(s): {missing}")
    print(f"Rows loaded for delete: {len(by_id)}")
    for pk in sorted(by_id):
        r = by_id[pk]
        notes = (r.notes or "").replace("\n", " ")[:100]
        print(
            f"  id={r.id}  {r.txn_date}  {r.txn_type!r}  "
            f"symbol={r.symbol!r}  qty={r.quantity}  total={r.total_amount}  "
            f"notes={notes!r}"
        )

    if not args.execute:
        print("\nDry-run only. Re-run with --execute to DELETE these rows.")
        return 0 if not missing else 1

    if missing:
        print("\nERROR: refusing --execute while some ids are missing (avoid partial surprises).", file=sys.stderr)
        return 1

    if args.backup_db:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bak = pipeline.config.DB_PATH.parent / (
            f"{pipeline.config.DB_PATH.name}.bak-del-inv-{ts}"
        )
        shutil.copy2(pipeline.config.DB_PATH, bak)
        print(f"\nBacked up DB to {bak}")

    with Session(engine) as session:
        for pk in want:
            row = session.get(InvestmentTransaction, pk)
            if row is not None:
                session.delete(row)
        session.commit()

    print(f"\nDeleted {len(want)} investment_transaction row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
