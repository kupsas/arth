#!/usr/bin/env python3
"""
Remove **duplicate** bank transactions that came from a Gmail PDF ingest, when another
row already represents the same spend.

Typical case: combined statement PDF email inserted rows even though InstaAlert +/or
``.txt`` import already had the same (date, amount, direction).  Path B2 is fixed to
prefer **exact calendar date** when matching statement rows, but old duplicates remain
until you delete them.

What gets deleted (only when ``--execute``):

  Rows where ``gmail_message_id`` matches ``--gmail-message-id`` **AND** there exists
  at least one **other** row with the same ``account_id``, ``txn_date``, ``amount``,
  and ``direction``.

Rows from that Gmail message that are **PDF-only** (no twin) are **kept** so you do
not lose data that only exists on the statement.

Always **back up** ``data/arth.db`` before ``--execute``.

Examples::

    # Preview (default): which ids would be removed
    python3 scripts/archive/remove_duplicate_pdf_email_transactions.py \\
        --gmail-message-id 19d5011bb26f69ae

    # Actually delete
    python3 scripts/archive/remove_duplicate_pdf_email_transactions.py \\
        --gmail-message-id 19d5011bb26f69ae --execute

**Archived:** One-off repair for a fixed ingest bug. Prefer prevention via current
parsers; run only if you still have legacy duplicates to clean.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repo root: this file may live in scripts/ or scripts/archive/
_script_dir = Path(__file__).resolve().parent
REPO_ROOT = _script_dir.parent.parent if _script_dir.name == "archive" else _script_dir.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401, E402 — loads .env after path bootstrap

from sqlmodel import Session, col, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import Transaction  # noqa: E402


def _duplicate_candidates(session: Session, gmail_message_id: str) -> list[Transaction]:
    """PDF-email rows that have at least one other row with the same natural key."""
    cur = session.exec(
        select(Transaction).where(Transaction.gmail_message_id == gmail_message_id)
    )
    pdf_rows = list(cur.all())
    out: list[Transaction] = []
    for t in pdf_rows:
        others = session.exec(
            select(Transaction)
            .where(Transaction.account_id == t.account_id)
            .where(Transaction.txn_date == t.txn_date)
            .where(Transaction.amount == t.amount)
            .where(Transaction.direction == t.direction)
            .where(col(Transaction.id) != t.id)
        ).all()
        if len(list(others)) >= 1:
            out.append(t)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--gmail-message-id",
        required=True,
        help="Gmail message id stamped on the unwanted PDF-ingest rows.",
    )
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Perform DELETE; default is dry-run (list only).",
    )
    ap.add_argument(
        "--backup-db",
        action="store_true",
        help=f"Copy {pipeline.config.DB_PATH} to data/arth.db.bak-dedupe-<timestamp> before delete.",
    )
    args = ap.parse_args()
    gid = args.gmail_message_id.strip()

    init_db()
    engine = get_engine()

    with Session(engine) as session:
        dupes = _duplicate_candidates(session, gid)

    print(f"gmail_message_id={gid!r}")
    print(f"Rows from this message that are duplicates (twin exists): {len(dupes)}")
    for t in sorted(dupes, key=lambda x: (x.txn_date, x.id or 0)):
        desc = (t.raw_description or "")[:72].replace("\n", " ")
        print(
            f"  id={t.id}  {t.txn_date}  {t.direction}  {t.amount}  |  {desc!r}"
        )

    if not args.execute:
        print("\nDry-run only. Re-run with --execute to DELETE these rows.")
        return

    if args.backup_db:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bak = REPO_ROOT / "data" / f"arth.db.bak-dedupe-{ts}"
        shutil.copy2(pipeline.config.DB_PATH, bak)
        print(f"\nBacked up DB to {bak}")

    with Session(engine) as session:
        dupes = _duplicate_candidates(session, gid)
        ids = [t.id for t in dupes if t.id is not None]
        for t in dupes:
            session.delete(t)
        session.commit()
        print(f"\nDeleted {len(ids)} row(s).")

    print(
        "\nTip: ``processed_emails`` still lists this Gmail id — the scraper will "
        "skip re-fetching. If you re-process the same email after fixing B2, you may "
        "need to remove that processed_emails row manually."
    )


if __name__ == "__main__":
    main()
