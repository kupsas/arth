"""
backfill_llm_spend_category.py

Runs LLM classification to fill spend_category for existing OUTFLOW transactions
where it's still NULL (i.e. rules couldn't determine NEED/WANT/INVESTMENT).

Skips:
  - INFLOW transactions (income has no spend_category by design)
  - Friends & Family counterparty — intentionally left NULL for manual tagging
  - Transactions that already have a spend_category

The LLM only sees transactions with NULL spend_category, so it will only
fill that one field per transaction (all other fields are pre-populated from
the DB, so _fields_needed() returns ["spend_category"] only).

Usage:
    python scripts/backfill_llm_spend_category.py            # runs on all eligible txns
    python scripts/backfill_llm_spend_category.py --dry-run  # preview without writing
"""

from __future__ import annotations

import argparse
import datetime
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from api.database import get_engine
from api.models import Transaction
from pipeline.llm_classifier import classify_llm
from pipeline.models import (
    CanonicalTransaction,
    Channel,
    CounterpartyCategory,
    Direction,
    SpendCategory,
    TxnType,
    UPIType,
)


def _db_txn_to_canonical(db_txn: Transaction) -> CanonicalTransaction:
    """Convert a DB Transaction row into a CanonicalTransaction for the LLM.

    All already-classified fields are populated so _fields_needed() returns
    only ["spend_category"] — we're just asking the LLM to fill that one gap.
    """
    fake = CanonicalTransaction(
        txn_id=f"T_{db_txn.id:08d}",
        txn_date=db_txn.txn_date or datetime.date.today(),
        account_id=db_txn.account_id,
        source_statement=db_txn.source_statement or db_txn.account_id,
        direction=Direction(db_txn.direction),
        amount=Decimal(str(db_txn.amount)),
        raw_description=db_txn.raw_description,
    )

    # Populate existing classification data so LLM doesn't re-classify them
    try:
        if db_txn.txn_type:
            fake.txn_type = TxnType(db_txn.txn_type)
        if db_txn.channel:
            fake.channel = Channel(db_txn.channel)
        if db_txn.upi_type:
            fake.upi_type = UPIType(db_txn.upi_type)
        if db_txn.counterparty_category:
            fake.counterparty_category = CounterpartyCategory(db_txn.counterparty_category)
    except ValueError:
        pass  # unknown enum value — leave None, classifier will handle it

    fake.counterparty = db_txn.counterparty
    return fake


def run(dry_run: bool = False) -> None:
    engine = get_engine()

    with Session(engine) as session:
        # Fetch eligible transactions: OUTFLOW, no spend_category, not Friends & Family
        db_txns = session.exec(
            select(Transaction)
            .where(Transaction.direction == "OUTFLOW")
            .where(Transaction.spend_category.is_(None))  # type: ignore[union-attr]
            .where(Transaction.counterparty_category != "Friends and Family")
            .order_by(Transaction.txn_date)
        ).all()

        if not db_txns:
            print("Nothing to classify — all eligible transactions already have a spend_category.")
            return

        print(f"Found {len(db_txns)} transactions needing LLM spend_category classification.")
        if dry_run:
            print("(dry-run mode — will show what WOULD be classified but not write to DB)\n")

        # Convert to CanonicalTransaction objects and keep an index→db_txn mapping
        canonical_txns: list[CanonicalTransaction] = []
        for db_txn in db_txns:
            canonical_txns.append(_db_txn_to_canonical(db_txn))

        # Run LLM — only ask about spend_category (all other fields are already set)
        print("Running LLM classification (this may take a minute for large batches)…")
        classified = classify_llm(canonical_txns)

        # Tally and (optionally) persist results
        updated = 0
        still_null = 0

        for i, ctxn in enumerate(classified):
            db_txn = db_txns[i]
            if ctxn.spend_category is not None:
                if dry_run:
                    print(
                        f"  [{db_txn.id}] {db_txn.txn_date}  "
                        f"{db_txn.counterparty or '?':<30}  "
                        f"₹{float(db_txn.amount):>10,.0f}  "
                        f"→ {ctxn.spend_category.value}"
                    )
                else:
                    db_txn.spend_category = ctxn.spend_category.value
                    session.add(db_txn)
                updated += 1
            else:
                still_null += 1

        if not dry_run:
            session.commit()
            print(f"\nBackfill complete:")
            print(f"  Classified:  {updated}")
            print(f"  Still NULL:  {still_null}  (LLM couldn't determine — ambiguous transactions)")
        else:
            print(f"\nDry-run summary: would classify {updated}, leave {still_null} NULL.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM backfill for spend_category")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview classifications without writing to DB",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
