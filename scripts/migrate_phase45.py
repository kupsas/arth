"""
DB Migration Script — Phase 4.5 Intelligence + Growth

Run this script ONCE if you have an existing arth.db created before Phase 4.5.
It safely adds new columns, creates new tables, and backfills data.

If you are starting fresh (no arth.db yet), you do NOT need to run this —
`init_db()` (called when the API server starts) will create all tables with
the correct schema from scratch.

Usage:
    python scripts/migrate_phase45.py              # applies to data/arth.db
    APP_ENV=test python scripts/migrate_phase45.py # applies to data/arth_test.db

What it does:
    1. Adds `spend_category` column (TEXT, nullable) to transactions
    2. Creates the `recurring_patterns` table if not present
    3. Creates the `goals` table if not present

All operations are idempotent — safe to run multiple times.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sure imports resolve from the repo root regardless of where we run from
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import sqlalchemy
from sqlalchemy import text

from api.database import get_engine, init_db
from pipeline.config import DB_PATH


def column_exists(conn: sqlalchemy.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a SQLite table using PRAGMA."""
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result)


def table_exists(conn: sqlalchemy.Connection, table: str) -> bool:
    """Check if a table exists in the SQLite database."""
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    )
    return result.fetchone() is not None


def run_migration() -> None:
    print("\nArth DB Migration — Phase 4.5 Intelligence + Growth")
    print(f"Target DB: {DB_PATH}")

    if not DB_PATH.exists():
        print("  ✓ No existing DB found — running init_db() to create fresh schema.")
        init_db()
        print("  ✓ All tables created with full schema. No migration needed.")
        return

    engine = get_engine()

    with engine.begin() as conn:

        # ── 1. Add spend_category to transactions ────────────────────────────
        # Nullable TEXT column — filled by pipeline or LLM on new transactions.
        # Existing transactions will have NULL until a backfill is run.
        if not column_exists(conn, "transactions", "spend_category"):
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN spend_category TEXT"
            ))
            print("  ✓ Added spend_category column to transactions")
        else:
            print("  · spend_category column already exists — skipping")

    # ── 2 & 3. Create new tables via init_db() ───────────────────────────────
    # init_db() calls SQLModel.metadata.create_all() which is a no-op for
    # tables that already exist.  The new RecurringPattern and Goal SQLModel
    # classes are now registered in api/models.py, so calling init_db() here
    # will create them if they don't yet exist.
    init_db()
    print("  ✓ Ensured recurring_patterns table exists (via init_db)")
    print("  ✓ Ensured goals table exists (via init_db)")

    print("\nMigration complete.\n")
    print("Next step: run the backfill script to populate spend_category for")
    print("existing transactions using rules-based classification:")
    print("  python scripts/backfill_spend_category.py\n")


if __name__ == "__main__":
    run_migration()
