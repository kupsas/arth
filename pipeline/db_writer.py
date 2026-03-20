"""
Write pipeline output (list[CanonicalTransaction]) to the SQLite database.

Core responsibilities:
  1. Compute a content_hash for each transaction (SHA-256 of the "natural key")
  2. Insert new rows; for existing rows, backfill classification fields that
     are still NULL without overwriting values set by earlier runs or manual edits
  3. Track the pipeline run in the pipeline_runs table
  4. (Phase 4) Reconcile statement rows against existing email-sourced rows
     to avoid duplicates and preserve manual edits

The content_hash ensures that re-running the pipeline on the same statement
file never creates duplicates.  For the email path, a different dedup
mechanism is used: an email-sourced transaction's content_hash is computed
from a different raw_description (the short alert text), so it will never
accidentally match a statement row's hash.  Reconciliation handles that case
explicitly via fuzzy matching on (account_id, amount, txn_date ± 1 day).

source_type values:
  "statement"  — default; inserted by the file-based pipeline
  "email"      — inserted by the Gmail scraper (is_reviewed=False)
  "reconciled" — was email-sourced, then upgraded when matching statement arrived
"""

from __future__ import annotations

import datetime
import hashlib

from sqlmodel import Session, select

from api.models import PipelineRun, Transaction
from pipeline.models import CanonicalTransaction


def compute_content_hash(txn: CanonicalTransaction) -> str:
    """Deterministic hash from the fields that uniquely identify a transaction.

    Uses txn_date | raw_description | amount | account_id as the composite
    natural key.  Two rows with the same hash represent the same real-world
    transaction (even if classified differently across pipeline runs).
    """
    key = "|".join([
        txn.txn_date.isoformat(),
        txn.raw_description,
        str(txn.amount),
        txn.account_id,
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


_BACKFILL_FIELDS: list[tuple[str, str]] = [
    # (CanonicalTransaction attr, Transaction DB column)
    # These are the classification fields that can be incrementally enriched.
    # Core identity fields (txn_date, amount, raw_description, etc.) are never touched.
    ("txn_type",              "txn_type"),
    ("channel",               "channel"),
    ("upi_type",              "upi_type"),
    ("counterparty",          "counterparty"),
    ("counterparty_category", "counterparty_category"),
]


def _resolve_value(txn: CanonicalTransaction, attr: str) -> str | None:
    """Read a classification field from a CanonicalTransaction, converting enums to strings."""
    val = getattr(txn, attr, None)
    if val is None:
        return None
    return val.value if hasattr(val, "value") else str(val)


def _find_email_match(
    session: Session,
    txn: CanonicalTransaction,
    account_id: str,
) -> Transaction | None:
    """Look for an unreconciled email-sourced row that matches this statement transaction.

    Matching criteria:
      - Same account
      - Exact amount match
      - Date within ±1 day (email alert and statement often land on different calendar days
        due to cutoff times, especially for late-night UPI payments)
      - source_type = 'email' (already-reconciled rows are excluded)

    Returns the first match found, or None.

    Edge case — two transactions with the same amount on the same day (e.g. two ₹200
    Swiggy orders): we match the first unreconciled email row.  The statement narration
    will overwrite the raw_description anyway, so the end result is always correct even
    if we matched the "wrong" email row.
    """
    one_day = datetime.timedelta(days=1)
    return session.exec(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.amount == float(txn.amount),
            Transaction.txn_date >= txn.txn_date - one_day,
            Transaction.txn_date <= txn.txn_date + one_day,
            Transaction.source_type == "email",
        )
    ).first()


def _upgrade_email_row(
    existing: Transaction,
    txn: CanonicalTransaction,
    content_hash: str,
    pipeline_run_id: int,
) -> None:
    """Upgrade an email-sourced row with richer statement data.

    What gets overwritten (statement data is authoritative for these):
      - raw_description, ref_number, closing_balance, content_hash, txn_date
      - txn_type, channel, upi_type (if the pipeline resolved them)
      - source_type → 'reconciled', is_reviewed → True

    What is PRESERVED (user's manual work must never be lost):
      - counterparty, counterparty_category, notes  (kept if already non-null)

    The pipeline_run_id is updated so the audit trail links to this statement run.
    """
    existing.raw_description = txn.raw_description
    existing.ref_number = txn.ref_number
    existing.closing_balance = float(txn.closing_balance) if txn.closing_balance else None
    existing.content_hash = content_hash
    existing.txn_date = txn.txn_date
    existing.source_type = "reconciled"
    existing.is_reviewed = True
    existing.pipeline_run_id = pipeline_run_id
    existing.updated_at = datetime.datetime.now(datetime.UTC)

    # Overwrite classification only if the DB value is still NULL —
    # same conservative logic as the normal backfill path.
    for canon_attr, db_col in _BACKFILL_FIELDS:
        if getattr(existing, db_col) is not None:
            continue
        new_val = _resolve_value(txn, canon_attr)
        if new_val is not None:
            setattr(existing, db_col, new_val)


def write_to_db(
    txns: list[CanonicalTransaction],
    *,
    source_key: str,
    llm_model: str,
    session: Session,
    source_type: str = "statement",
    gmail_message_id: str | None = None,
) -> PipelineRun:
    """Insert new transactions and backfill NULLs on existing ones.

    Args:
        txns:              Fully enriched transactions from the pipeline.
        source_key:        Which source config was used (e.g. "hdfc_savings").
        llm_model:         LLM model that was used (or "none").
        session:           An open SQLModel Session (caller manages the engine).
        source_type:       Where did these transactions come from?
                           "statement" (default) — file-based pipeline, is_reviewed=True
                           "email"               — Gmail scraper, is_reviewed=False,
                                                   reconciliation is skipped (these ARE
                                                   the email rows, not the statement rows)
        gmail_message_id:  The Gmail message ID that produced these transactions.
                           Only meaningful when source_type="email"; stored on each row
                           so we can trace a transaction back to its source email.

    Returns:
        The PipelineRun row with final counts and status.

    Reconciliation (statement path only):
        When source_type="statement", before inserting a new row the writer checks
        whether a matching email-sourced row already exists
        (same account_id, exact amount, date ±1 day).  If found, that row is
        *upgraded* with the richer statement data instead of inserting a duplicate.
        This is the mechanism that prevents statement uploads from creating ghost
        duplicates of transactions the email scraper already captured.
    """
    # Create the audit-trail row first so we can link transactions to it.
    run = PipelineRun(
        source_key=source_key,
        llm_model=llm_model,
        status="running",
    )
    session.add(run)
    session.flush()  # assigns run.id without committing
    assert run.id is not None  # flush guarantees this; tells mypy the id is set

    # Email-sourced transactions enter as unreviewed; statement-sourced are reviewed.
    is_reviewed_default = source_type != "email"

    new_count = 0
    updated_count = 0
    reconciled_count = 0
    date_min: datetime.date | None = None
    date_max: datetime.date | None = None

    for txn in txns:
        content_hash = compute_content_hash(txn)

        # ── Path A: exact content_hash match ────────────────────────────────
        # This handles same-source dedup (re-running the same statement file,
        # or the same email coming in twice).  It's the fast path.
        existing = session.exec(
            select(Transaction).where(Transaction.content_hash == content_hash)
        ).first()

        if existing is not None:
            # Backfill: fill in NULL classification fields without clobbering
            # any values that were set by earlier runs or manual user edits.
            fields_touched = 0
            for canon_attr, db_col in _BACKFILL_FIELDS:
                if getattr(existing, db_col) is not None:
                    continue
                new_val = _resolve_value(txn, canon_attr)
                if new_val is not None:
                    setattr(existing, db_col, new_val)
                    fields_touched += 1

            if fields_touched > 0:
                existing.updated_at = datetime.datetime.now(datetime.UTC)
                session.add(existing)
                updated_count += 1
            continue

        # ── Path B: reconciliation (statement path only) ─────────────────────
        # A statement row may describe the same real-world transaction as an
        # existing email-sourced row, but with a completely different
        # raw_description (e.g. "UPI-SWIGGY-..." vs "Rs 450 debited via UPI").
        # The content_hash check above won't catch this, so we do a fuzzy match.
        if source_type == "statement":
            account_id = txn.account_id
            email_match = _find_email_match(session, txn, account_id)
            if email_match is not None:
                _upgrade_email_row(email_match, txn, content_hash, run.id)
                session.add(email_match)
                reconciled_count += 1

                if date_min is None or txn.txn_date < date_min:
                    date_min = txn.txn_date
                if date_max is None or txn.txn_date > date_max:
                    date_max = txn.txn_date
                continue

        # ── Path C: brand-new row ────────────────────────────────────────────
        db_txn = Transaction(
            content_hash=content_hash,
            txn_date=txn.txn_date,
            account_id=txn.account_id,
            source_statement=txn.source_statement,
            direction=txn.direction.value,
            amount=float(txn.amount),
            currency=txn.currency,
            txn_type=txn.txn_type.value if txn.txn_type else None,
            channel=txn.channel.value if txn.channel else None,
            upi_type=txn.upi_type.value if txn.upi_type else None,
            counterparty=txn.counterparty,
            counterparty_category=(
                txn.counterparty_category.value if txn.counterparty_category else None
            ),
            raw_description=txn.raw_description,
            ref_number=txn.ref_number,
            closing_balance=float(txn.closing_balance) if txn.closing_balance else None,
            value_date=txn.value_date,
            notes=txn.notes,
            is_reviewed=is_reviewed_default,
            pipeline_run_id=run.id,
            source_type=source_type,
            gmail_message_id=gmail_message_id,
        )
        session.add(db_txn)
        new_count += 1

        if date_min is None or txn.txn_date < date_min:
            date_min = txn.txn_date
        if date_max is None or txn.txn_date > date_max:
            date_max = txn.txn_date

    # Finalise the pipeline run row.
    # We store reconciled_count inside updated_count so the existing API
    # response shape doesn't change — the field already means "rows that were
    # touched but not inserted fresh".
    run.txn_count = len(txns)
    run.new_count = new_count
    run.updated_count = updated_count + reconciled_count
    run.txn_date_min = date_min
    run.txn_date_max = date_max
    run.status = "completed"
    run.completed_at = datetime.datetime.now(datetime.UTC)

    session.commit()
    session.refresh(run)
    return run
