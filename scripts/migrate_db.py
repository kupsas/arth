"""
DB Migration Script — Phase 4 Email Scraper

Run this script ONCE if you have an existing arth_main.db (or legacy arth_v1.db) that was created before
Phase 4.  It safely adds the new columns and index to the transactions table
and creates the new processed_emails table.

If you are starting fresh (no database file yet), you do NOT need to run this —
`init_db()` (called when the API server starts) will create all tables with
the correct schema from scratch.

Usage:
    python scripts/migrate_db.py              # applies to data/arth_main.db
    APP_ENV=test python scripts/migrate_db.py # applies to data/arth_test.db

What it does:
    1. Adds `source_type` column (TEXT, default 'statement') to transactions
    2. Adds `gmail_message_id` column (TEXT, nullable) to transactions
    3. Creates composite index ix_txn_reconciliation if not present
    4. Creates the `processed_emails` table if not present
    5. Backfills `source_type = 'statement'` for any rows that are NULL
       (in case the column was added without a default in a previous partial run)

All operations are idempotent — safe to run multiple times.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sure imports resolve from the repo root regardless of where we run from
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import sqlalchemy
from sqlalchemy import inspect, text

from api.database import get_engine, init_db
from pipeline.config import DB_PATH


def column_exists(conn: sqlalchemy.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a SQLite table using PRAGMA."""
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result)


def index_exists(conn: sqlalchemy.Connection, table: str, index_name: str) -> bool:
    """Check if an index exists on a SQLite table using PRAGMA."""
    result = conn.execute(text(f"PRAGMA index_list({table})"))
    return any(row[1] == index_name for row in result)


def table_exists(conn: sqlalchemy.Connection, table: str) -> bool:
    """Check if a table exists in the SQLite database."""
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    )
    return result.fetchone() is not None


def run_migration() -> None:
    print(f"\nArth DB Migration — Phase 4 Email Scraper")
    print(f"Target DB: {DB_PATH}")

    if not DB_PATH.exists():
        print("  ✓ No existing DB found — running init_db() to create fresh schema.")
        init_db()
        print("  ✓ All tables created with full schema. No migration needed.")
        return

    engine = get_engine()

    with engine.begin() as conn:

        # ── 1. Add source_type to transactions ──────────────────────────────
        if not column_exists(conn, "transactions", "source_type"):
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN source_type TEXT NOT NULL DEFAULT 'statement'"
            ))
            print("  ✓ Added source_type column to transactions")
        else:
            print("  · source_type column already exists — skipping")

        # ── 2. Add gmail_message_id to transactions ──────────────────────────
        if not column_exists(conn, "transactions", "gmail_message_id"):
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN gmail_message_id TEXT"
            ))
            print("  ✓ Added gmail_message_id column to transactions")
        else:
            print("  · gmail_message_id column already exists — skipping")

        # ── 3. Create reconciliation index ───────────────────────────────────
        if not index_exists(conn, "transactions", "ix_txn_reconciliation"):
            conn.execute(text(
                "CREATE INDEX ix_txn_reconciliation "
                "ON transactions (account_id, amount, txn_date, source_type)"
            ))
            print("  ✓ Created ix_txn_reconciliation index on transactions")
        else:
            print("  · ix_txn_reconciliation already exists — skipping")

        # ── 4. Create gmail_message_id index on transactions ─────────────────
        # SQLModel declares this via Field(index=True); we need it for scraper lookups.
        if not index_exists(conn, "transactions", "ix_transactions_gmail_message_id"):
            conn.execute(text(
                "CREATE INDEX ix_transactions_gmail_message_id "
                "ON transactions (gmail_message_id)"
            ))
            print("  ✓ Created ix_transactions_gmail_message_id index")
        else:
            print("  · ix_transactions_gmail_message_id already exists — skipping")

        # ── 5. Backfill NULL source_type rows ────────────────────────────────
        # Safety net: if source_type was added without a DEFAULT in a prior run,
        # any pre-existing rows will have NULL.  Fix them now.
        result = conn.execute(
            text("UPDATE transactions SET source_type = 'statement' WHERE source_type IS NULL")
        )
        if result.rowcount:
            print(f"  ✓ Backfilled source_type='statement' on {result.rowcount} rows")

    # ── 6. Create processed_emails table ─────────────────────────────────────
    # init_db() calls create_all() which is a no-op for tables that already
    # exist, so we can safely call it here to create processed_emails.
    init_db()
    print("  ✓ Ensured processed_emails table exists (via init_db)")

    print("\nMigration complete.\n")


if __name__ == "__main__":
    run_migration()
