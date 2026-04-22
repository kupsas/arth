"""
Database engine, session factory, and initialisation for Arth.

Uses SQLModel (which wraps SQLAlchemy) with a synchronous SQLite backend.
The DB file path comes from pipeline.config.DB_PATH so both the API server
and the CLI pipeline share the same resolution logic.

For automated tests, callers override `get_session` via FastAPI dependency
injection to point at an in-memory SQLite database instead.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

from pipeline.config import DB_PATH, REPO_ROOT

logger = logging.getLogger(__name__)

# `check_same_thread=False` is required because FastAPI serves requests
# across multiple threads, but SQLite's default is single-thread only.
_engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 60},
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


def _index_exists(conn, name: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :n"),
        {"n": name},
    ).fetchone()
    return row is not None


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :t LIMIT 1"),
        {"t": table},
    ).fetchone()
    return row is not None


def _migrate_nse_equity_reference_schema(conn) -> None:
    """Align ``nse_equity_reference`` with newer ORM: nullable cap + ``instrument_kind``.

    Older DBs stored ``market_cap_class`` as NOT NULL; we need NULL for non-equities.
    SQLite cannot relax NOT NULL in-place, so we rebuild when that column is still strict.
    """
    if not _table_exists(conn, "nse_equity_reference"):
        return
    info_rows = conn.execute(text("PRAGMA table_info(nse_equity_reference)")).fetchall()
    cols = {r[1]: r for r in info_rows}
    mc = cols.get("market_cap_class")
    mc_not_null = mc is not None and mc[3] == 1
    has_kind = "instrument_kind" in cols

    if has_kind and not mc_not_null:
        return

    if not mc_not_null and not has_kind:
        conn.execute(
            text(
                "ALTER TABLE nse_equity_reference ADD COLUMN instrument_kind "
                "TEXT NOT NULL DEFAULT 'UNKNOWN'"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_nse_equity_reference_instrument_kind "
                "ON nse_equity_reference (instrument_kind)"
            )
        )
        return

    insert_cols = [
        "symbol",
        "market_cap_class",
        "instrument_kind",
        "company_name",
        "industry",
        "isin",
        "last_price",
        "ffmc",
        "reference_json",
        "updated_at",
    ]
    select_bits: list[str] = []
    for c in insert_cols:
        if c == "instrument_kind":
            select_bits.append(
                "COALESCE(instrument_kind, 'UNKNOWN')" if has_kind else "'UNKNOWN'"
            )
        else:
            select_bits.append(c)

    conn.execute(text("DROP TABLE IF EXISTS nse_equity_reference__mig"))
    conn.execute(
        text(
            """
            CREATE TABLE nse_equity_reference__mig (
                symbol TEXT NOT NULL PRIMARY KEY,
                market_cap_class TEXT,
                instrument_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
                company_name TEXT,
                industry TEXT,
                isin TEXT,
                last_price REAL,
                ffmc REAL,
                reference_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            f"INSERT INTO nse_equity_reference__mig ({', '.join(insert_cols)}) "
            f"SELECT {', '.join(select_bits)} FROM nse_equity_reference"
        )
    )
    conn.execute(text("DROP TABLE nse_equity_reference"))
    conn.execute(text("ALTER TABLE nse_equity_reference__mig RENAME TO nse_equity_reference"))
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_nse_equity_reference_market_cap_class "
            "ON nse_equity_reference (market_cap_class)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_nse_equity_reference_isin "
            "ON nse_equity_reference (isin)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_nse_equity_reference_instrument_kind "
            "ON nse_equity_reference (instrument_kind)"
        )
    )


def _backfill_goal_chart_keys(conn) -> None:
    """One-time style updates: map legacy goals to dashboard chart_key (idempotent)."""
    if not _column_exists(conn, "goals", "chart_key"):
        return
    stmts = [
        (
            "UPDATE goals SET chart_key = 'expense_need_want_stack' "
            "WHERE goal_type = 'EXPENSE_LIMIT' AND chart_key IS NULL "
            "AND linked_category IS NULL"
        ),
        (
            "UPDATE goals SET chart_key = 'investment_net' "
            "WHERE goal_type = 'INVESTMENT' AND chart_key IS NULL"
        ),
        (
            "UPDATE goals SET chart_key = 'category:food_and_dining' "
            "WHERE goal_type = 'EXPENSE_LIMIT' AND chart_key IS NULL "
            "AND linked_category = 'Food & Dining'"
        ),
        (
            "UPDATE goals SET chart_key = 'category:shopping' "
            "WHERE goal_type = 'EXPENSE_LIMIT' AND chart_key IS NULL "
            "AND linked_category = 'Shopping & E-commerce'"
        ),
        (
            "UPDATE goals SET chart_key = 'category:transport' "
            "WHERE goal_type = 'EXPENSE_LIMIT' AND chart_key IS NULL "
            "AND linked_category = 'Transport & Fuel'"
        ),
        (
            "UPDATE goals SET chart_key = 'category:travel' "
            "WHERE goal_type = 'EXPENSE_LIMIT' AND chart_key IS NULL "
            "AND linked_category = 'Travel & Stay'"
        ),
        (
            "UPDATE goals SET chart_key = 'category:gifts' "
            "WHERE goal_type = 'EXPENSE_LIMIT' AND chart_key IS NULL "
            "AND linked_category = 'Gifts & Personal Transfers'"
        ),
    ]
    for sql in stmts:
        conn.execute(text(sql))


def _backfill_transaction_user_ids(conn) -> None:
    """Set transactions.user_id from account→user mapping (legacy DBs + any NULL rows)."""
    if not _column_exists(conn, "transactions", "user_id"):
        return
    from api.services.account_user_map import user_id_for_account

    rows = conn.execute(
        text(
            "SELECT DISTINCT account_id FROM transactions "
            "WHERE user_id IS NULL OR TRIM(user_id) = ''"
        )
    ).fetchall()
    for (account_id,) in rows:
        if not account_id:
            continue
        uid = user_id_for_account(str(account_id))
        conn.execute(
            text(
                "UPDATE transactions SET user_id = :uid "
                "WHERE account_id = :aid AND (user_id IS NULL OR TRIM(user_id) = '')"
            ),
            {"uid": uid, "aid": str(account_id)},
        )


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
        if not _column_exists(conn, "goals", "chart_key"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN chart_key TEXT"))
        if not _column_exists(conn, "goals", "progress_cadence"):
            conn.execute(
                text(
                    "ALTER TABLE goals ADD COLUMN progress_cadence TEXT "
                    "NOT NULL DEFAULT 'MONTHLY'"
                )
            )
        conn.execute(
            text(
                "UPDATE goals SET progress_cadence = 'MONTHLY' "
                "WHERE progress_cadence IS NULL OR TRIM(progress_cadence) = ''"
            )
        )
        _backfill_goal_chart_keys(conn)
        if not _column_exists(conn, "reminders", "example_transaction_ids"):
            conn.execute(
                text("ALTER TABLE reminders ADD COLUMN example_transaction_ids TEXT")
            )
        if not _column_exists(conn, "reminders", "description_match_anchors"):
            conn.execute(
                text("ALTER TABLE reminders ADD COLUMN description_match_anchors TEXT")
            )
        # Phase A.0 — link bank transactions to holdings (e.g. dividend → equity position).
        if not _column_exists(conn, "transactions", "holding_id"):
            conn.execute(text("ALTER TABLE transactions ADD COLUMN holding_id INTEGER"))

        # Phase B.0 — goal pyramid / activation columns (additive, safe for existing rows).
        if not _column_exists(conn, "goals", "pyramid_id"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN pyramid_id TEXT"))
        if not _column_exists(conn, "goals", "tier"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN tier TEXT"))
        if not _column_exists(conn, "goals", "time_horizon"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN time_horizon TEXT"))
        if not _column_exists(conn, "goals", "funding_mode"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN funding_mode TEXT"))
        if not _column_exists(conn, "goals", "activation_status"):
            conn.execute(
                text(
                    "ALTER TABLE goals ADD COLUMN activation_status TEXT "
                    "NOT NULL DEFAULT 'ACTIVE'"
                )
            )
        if not _column_exists(conn, "goals", "activation_condition"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN activation_condition TEXT"))
        if not _column_exists(conn, "goals", "monthly_allocation"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN monthly_allocation REAL"))
        if not _column_exists(conn, "goals", "allocation_priority"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN allocation_priority INTEGER"))
        if not _column_exists(conn, "goals", "interruptible"):
            conn.execute(
                text("ALTER TABLE goals ADD COLUMN interruptible INTEGER NOT NULL DEFAULT 1")
            )
        if not _column_exists(conn, "goals", "sensitivity_to_returns"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN sensitivity_to_returns TEXT"))

        # Retire PAUSED activation (Track 3): map existing rows to ACTIVE; progress % shows gaps.
        if _column_exists(conn, "goals", "activation_status"):
            conn.execute(
                text(
                    "UPDATE goals SET activation_status = 'ACTIVE' "
                    "WHERE UPPER(TRIM(activation_status)) = 'PAUSED'"
                )
            )

        # Enforce pyramid_id uniqueness per user when set (SQLite treats NULLs as distinct).
        if not _index_exists(conn, "uq_goals_user_pyramid_id"):
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_goals_user_pyramid_id "
                    "ON goals (user_id, pyramid_id)"
                )
            )

        # Holdings page — classification columns (B1); nullable, backfilled by enrichment.
        if not _column_exists(conn, "holdings", "sector"):
            conn.execute(text("ALTER TABLE holdings ADD COLUMN sector TEXT"))
        if not _column_exists(conn, "holdings", "market_cap_class"):
            conn.execute(text("ALTER TABLE holdings ADD COLUMN market_cap_class TEXT"))
        if not _column_exists(conn, "holdings", "fund_category"):
            conn.execute(text("ALTER TABLE holdings ADD COLUMN fund_category TEXT"))
        if not _column_exists(conn, "holdings", "fund_house"):
            conn.execute(text("ALTER TABLE holdings ADD COLUMN fund_house TEXT"))

        # Goals architecture V2 — classification, recurrence, inflation, priority (Sub-Plan A).
        if not _column_exists(conn, "goals", "goal_class"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN goal_class TEXT"))
        if not _column_exists(conn, "goals", "recurrence_amount"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN recurrence_amount REAL"))
        if not _column_exists(conn, "goals", "recurrence_frequency"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN recurrence_frequency TEXT"))
        if not _column_exists(conn, "goals", "recurrence_start"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN recurrence_start TEXT"))
        if not _column_exists(conn, "goals", "recurrence_end"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN recurrence_end TEXT"))
        if not _column_exists(conn, "goals", "goal_specific_inflation_rate"):
            conn.execute(
                text("ALTER TABLE goals ADD COLUMN goal_specific_inflation_rate REAL")
            )
        if not _column_exists(conn, "goals", "expected_return_rate"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN expected_return_rate REAL"))
        if not _column_exists(conn, "goals", "starting_balance"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN starting_balance REAL"))
        if not _column_exists(conn, "goals", "system_priority_score"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN system_priority_score REAL"))
        if not _column_exists(conn, "goals", "goal_subtype"):
            conn.execute(text("ALTER TABLE goals ADD COLUMN goal_subtype TEXT"))

        if not _column_exists(conn, "holdings", "earliest_liquidity_date"):
            conn.execute(
                text("ALTER TABLE holdings ADD COLUMN earliest_liquidity_date TEXT")
            )

        # Phase 5 — investment txn review queue (same semantics as Transaction.is_reviewed).
        if not _column_exists(conn, "investment_transactions", "is_reviewed"):
            conn.execute(
                text(
                    "ALTER TABLE investment_transactions ADD COLUMN is_reviewed "
                    "INTEGER NOT NULL DEFAULT 1"
                )
            )
        if not _column_exists(conn, "investment_transactions", "source_type"):
            conn.execute(
                text("ALTER TABLE investment_transactions ADD COLUMN source_type TEXT")
            )
        if not _column_exists(conn, "investment_transactions", "gmail_message_id"):
            conn.execute(
                text(
                    "ALTER TABLE investment_transactions ADD COLUMN gmail_message_id TEXT"
                )
            )
        if not _column_exists(conn, "investment_transactions", "updated_at"):
            conn.execute(text("ALTER TABLE investment_transactions ADD COLUMN updated_at TEXT"))
        conn.execute(
            text(
                "UPDATE investment_transactions SET updated_at = created_at "
                "WHERE updated_at IS NULL"
            )
        )
        if not _index_exists(conn, "ix_investment_transactions_is_reviewed"):
            conn.execute(
                text(
                    "CREATE INDEX ix_investment_transactions_is_reviewed "
                    "ON investment_transactions (is_reviewed)"
                )
            )

        # Sub-Plan B — recurring patterns scoped per user (surplus / goals).
        if not _column_exists(conn, "recurring_patterns", "user_id"):
            conn.execute(
                text(
                    "ALTER TABLE recurring_patterns ADD COLUMN user_id TEXT NOT NULL DEFAULT 'sashank'"
                )
            )
        conn.execute(
            text(
                "UPDATE recurring_patterns SET user_id = 'sashank' "
                "WHERE user_id IS NULL OR TRIM(user_id) = ''"
            )
        )
        if not _index_exists(conn, "uq_recurring_pattern_user_cp_dir_freq"):
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_recurring_pattern_user_cp_dir_freq "
                    "ON recurring_patterns (user_id, counterparty, direction, frequency)"
                )
            )

        # Desktop pre-req: explicit user on each bank txn row (was inferred via account_id only).
        if not _column_exists(conn, "transactions", "user_id"):
            conn.execute(text("ALTER TABLE transactions ADD COLUMN user_id TEXT"))
        _backfill_transaction_user_ids(conn)
        if not _index_exists(conn, "ix_transactions_user_id"):
            conn.execute(
                text("CREATE INDEX ix_transactions_user_id ON transactions (user_id)")
            )

        if not _column_exists(conn, "transactions", "classification_source"):
            conn.execute(text("ALTER TABLE transactions ADD COLUMN classification_source TEXT"))
        if not _column_exists(conn, "transactions", "review_confidence"):
            conn.execute(text("ALTER TABLE transactions ADD COLUMN review_confidence TEXT"))

        _migrate_nse_equity_reference_schema(conn)


def _merge_starter_pack_for_all_users() -> None:
    """Seed ``user_merchant_rules`` from ``data/merchant_starter_pack.json`` for each user."""
    try:
        from api.services.user_classification import merge_starter_pack_for_all_users

        merge_starter_pack_for_all_users()
    except Exception:
        logger.exception("Starter merchant pack merge skipped or failed")


def _chmod_owner_rw_only(path: Path) -> None:
    """Best-effort ``0o600`` (owner read/write only). No-op if missing or OS rejects chmod."""
    try:
        if path.is_file():
            os.chmod(path, 0o600)
    except OSError:
        # Windows / exotic FS may ignore or reject mode bits — DB still works.
        pass


def _seed_desktop_prereq_defaults() -> None:
    """Seed app_users + scraper config from env / scraper.config when tables are empty."""
    import bcrypt
    from sqlmodel import Session, select

    from api.models import AppUser, ScraperAccountMapping, ScraperBankSender
    from scraper.config import BANK_SENDERS

    try:
        with Session(_engine) as session:
            if session.exec(select(AppUser)).first() is None:
                raw_user = (os.getenv("AUTH_USERNAME") or "sashank").strip()
                raw_pw = (os.getenv("AUTH_PASSWORD") or "").strip()
                if raw_pw:
                    pw_hash = bcrypt.hashpw(
                        raw_pw.encode("utf-8"),
                        bcrypt.gensalt(rounds=12),
                    ).decode("ascii")
                    session.add(
                        AppUser(
                            username=raw_user,
                            password_hash=pw_hash,
                            setup_completed_at=None,
                        )
                    )
                    session.commit()
                    logger.info("Seeded default app_users row for %r", raw_user)

            if session.exec(select(ScraperBankSender)).first() is None:
                uid = (os.getenv("AUTH_USERNAME") or "sashank").strip()
                for sender_email, cfg in BANK_SENDERS.items():
                    pk = cfg.get("parser_key")
                    session.add(
                        ScraperBankSender(
                            user_id=uid,
                            sender_email=sender_email.strip().lower(),
                            parser_key=str(pk) if pk else None,
                            first_run_lookback_days=cfg.get("first_run_lookback_days"),
                            enabled=True,
                        )
                    )
                    accounts = cfg.get("accounts") or {}
                    for last_4, acct in accounts.items():
                        session.add(
                            ScraperAccountMapping(
                                user_id=uid,
                                sender_email=sender_email.strip().lower(),
                                last_4_digits=str(last_4),
                                account_id=str(acct["account_id"]),
                                source_key=str(acct["source_key"]),
                            )
                        )
                session.commit()
                logger.info(
                    "Seeded scraper_bank_senders / scraper_account_mappings from scraper.config"
                )
    except Exception:
        logger.exception("Desktop prereq seed skipped or failed")


def _sync_missing_bank_senders_from_config() -> None:
    """Insert any ``BANK_SENDERS`` keys that exist in code but not yet in SQLite.

    Initial scraper seed runs only when ``scraper_bank_senders`` is completely
    empty, so new senders added to :data:`scraper.config.BANK_SENDERS` later would
    otherwise never be polled. This runs on every ``init_db()`` and only adds
    missing (user_id, sender_email) rows plus account mappings — idempotent.
    """
    from sqlmodel import Session, select

    from api.models import ScraperAccountMapping, ScraperBankSender
    from scraper.config import BANK_SENDERS

    try:
        with Session(_engine) as session:
            # Only users who already use SQLite for scraper config (≥1 row).
            # Others still get :data:`scraper.config.BANK_SENDERS` in memory — no merge needed.
            all_rows = session.exec(select(ScraperBankSender)).all()
            if not all_rows:
                return

            user_ids = {row.user_id for row in all_rows}

            total_new = 0
            for uid in sorted(user_ids):
                existing = {
                    r.sender_email.strip().lower()
                    for r in session.exec(
                        select(ScraperBankSender).where(ScraperBankSender.user_id == uid)
                    ).all()
                }
                for sender_email, cfg in BANK_SENDERS.items():
                    key = sender_email.strip().lower()
                    if key in existing:
                        continue
                    pk = cfg.get("parser_key")
                    session.add(
                        ScraperBankSender(
                            user_id=uid,
                            sender_email=key,
                            parser_key=str(pk) if pk else None,
                            first_run_lookback_days=cfg.get("first_run_lookback_days"),
                            enabled=True,
                        )
                    )
                    accounts = cfg.get("accounts") or {}
                    for last_4, acct in accounts.items():
                        session.add(
                            ScraperAccountMapping(
                                user_id=uid,
                                sender_email=key,
                                last_4_digits=str(last_4),
                                account_id=str(acct["account_id"]),
                                source_key=str(acct["source_key"]),
                            )
                        )
                    existing.add(key)
                    total_new += 1

            if total_new:
                session.commit()
                logger.info(
                    "Synced %d new scraper_bank_senders row(s) from scraper.config.BANK_SENDERS",
                    total_new,
                )
    except Exception:
        logger.exception("Sync of new bank senders from config skipped or failed")


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
    _seed_desktop_prereq_defaults()
    _sync_missing_bank_senders_from_config()
    _merge_starter_pack_for_all_users()
    # Phase A.5 — limit exposure of local secrets (SQLite file + Gmail OAuth token).
    _chmod_owner_rw_only(DB_PATH)
    _chmod_owner_rw_only(REPO_ROOT / "data" / "gmail_token.json")


def get_session():
    """FastAPI dependency — yields a DB session per request, auto-closes."""
    with Session(_engine) as session:
        yield session
