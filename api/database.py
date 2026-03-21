"""
Database engine, session factory, and initialisation for Arth.

Uses SQLModel (which wraps SQLAlchemy) with a synchronous SQLite backend.
The DB file path comes from pipeline.config.DB_PATH so both the API server
and the CLI pipeline share the same resolution logic.

For automated tests, callers override `get_session` via FastAPI dependency
injection to point at an in-memory SQLite database instead.
"""

from __future__ import annotations

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from pipeline.config import DB_PATH

# `check_same_thread=False` is required because FastAPI serves requests
# across multiple threads, but SQLite's default is single-thread only.
_engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(_engine, "connect")
def _sqlite_enable_wal(dbapi_conn, connection_record) -> None:
    """Use WAL journal so readers (API) and writers (scraper/pipeline) overlap better.

    Default rollback mode can block concurrent access; WAL allows reads during writes.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def get_engine():
    """Return the module-level engine (useful for tests that need to swap it)."""
    return _engine


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result)


def _apply_sqlite_patches() -> None:
    """Add columns/tables introduced after the DB was first created (SQLite ALTER)."""
    with _engine.begin() as conn:
        if not _column_exists(conn, "transactions", "exclude_from_analytics"):
            conn.execute(
                text(
                    "ALTER TABLE transactions ADD COLUMN exclude_from_analytics "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
        if not _column_exists(conn, "transactions", "exclusion_reason"):
            conn.execute(
                text("ALTER TABLE transactions ADD COLUMN exclusion_reason TEXT")
            )


def init_db() -> None:
    """Create all tables that don't already exist.

    Safe to call repeatedly — SQLModel/SQLAlchemy's create_all() is a no-op
    for tables that are already present.
    """
    # Import models so SQLModel registers their metadata before create_all().
    import api.models  # noqa: F401

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(_engine)
    _apply_sqlite_patches()


def get_session():
    """FastAPI dependency — yields a DB session per request, auto-closes."""
    with Session(_engine) as session:
        yield session
