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
import logging

from sqlalchemy import or_
from sqlmodel import Session, col, select

from api.models import PipelineRun, Transaction
from api.services.account_user_map import user_id_for_account
from api.services.goal_status_cache import invalidate_goal_status_cache
from pipeline.models import CanonicalTransaction, ClassificationSource
from pipeline.review_confidence import compute_review_confidence, should_auto_review_email

logger = logging.getLogger(__name__)


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
    ("spend_category",        "spend_category"),
    ("classification_source", "classification_source"),
]


def _resolve_value(txn: CanonicalTransaction, attr: str) -> str | None:
    """Read a classification field from a CanonicalTransaction, converting enums to strings."""
    val = getattr(txn, attr, None)
    if val is None:
        return None
    if isinstance(val, ClassificationSource):
        return val.value
    return val.value if hasattr(val, "value") else str(val)


def _path_a_backfill_existing(
    session: Session,
    existing: Transaction,
    txn: CanonicalTransaction,
    row_user_id: str | None,
) -> bool:
    """Fill NULL classification columns on ``existing`` from ``txn``. Return True if anything changed."""
    fields_touched = 0
    if not existing.user_id:
        existing.user_id = row_user_id
        fields_touched += 1
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
    return fields_touched > 0


def _find_statement_match_for_email(
    session: Session,
    txn: CanonicalTransaction,
    account_id: str,
) -> Transaction | None:
    """If a CSV/statement row already represents this spend, skip inserting an email PDF duplicate.

    Same core idea as :func:`_find_email_match`, inverted: email ingest checks for an
    existing **statement** or **reconciled** row with the same account, amount,
    direction, and txn_date within ±1 day.  Narration differs (PDF vs export), so
    :func:`compute_content_hash` will not dedupe.

    **Matching order (important):**

    1. **Exact calendar date** — if exactly one statement row matches, return it.
       This fixes recurring identical amounts on **consecutive days** (e.g. ₹1,000
       UPI-Lite): a ±1-day window alone can match **two** statement rows (yesterday +
       today), so ``len == 2`` and we used to fall through and **insert** a duplicate
       email PDF row for each.

    2. **±1 day window** — only if step 1 finds zero rows: then if exactly one
       statement row falls in the window (typical value-date / midnight cutoff),
       return it.

    3. Otherwise return ``None`` (ambiguous or no match — insert the email row).

    If **more than one** row matches on the **same** calendar date (two Swiggy orders),
    returns ``None`` — safer than dropping a real second charge.
    """
    uid = user_id_for_account(account_id, session)
    base = (
        select(Transaction)
        .where(Transaction.account_id == account_id)
        .where(
            or_(
                col(Transaction.user_id) == uid,
                col(Transaction.user_id).is_(None),
            )
        )
        .where(Transaction.amount == float(txn.amount))
        .where(Transaction.direction == txn.direction.value)
        .where(col(Transaction.source_type).in_(["statement", "reconciled"]))
    )
    exact = list(
        session.exec(base.where(Transaction.txn_date == txn.txn_date)).all()
    )
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    one_day = datetime.timedelta(days=1)
    window = list(
        session.exec(
            base.where(Transaction.txn_date >= txn.txn_date - one_day)
            .where(Transaction.txn_date <= txn.txn_date + one_day)
        ).all()
    )
    if len(window) == 1:
        return window[0]
    return None


def _find_prior_email_alert_match_for_pdf(
    session: Session,
    txn: CanonicalTransaction,
    account_id: str,
    *,
    exclude_gmail_message_id: str | None,
) -> Transaction | None:
    """If a **transaction-alert** (or other) email row already captured this spend, skip PDF duplicate.

    Path B2 / :func:`_find_statement_match_for_email` only compares against
    ``statement`` / ``reconciled`` rows.  Monthly statement PDFs often duplicate
    transactions that arrived earlier as **per-txn HTML emails** (same
    ``source_type='email'`` but different ``gmail_message_id`` and narration).

    Uses the same **exact date first, then ±1 day** rule as statement matching.
    Rows whose ``gmail_message_id`` equals ``exclude_gmail_message_id`` are ignored
    so two different lines **inside the same PDF email** (e.g. two ₹500 charges the
    same day) do not suppress each other.
    """
    if not exclude_gmail_message_id:
        return None

    uid = user_id_for_account(account_id, session)
    base = (
        select(Transaction)
        .where(Transaction.account_id == account_id)
        .where(
            or_(
                col(Transaction.user_id) == uid,
                col(Transaction.user_id).is_(None),
            )
        )
        .where(Transaction.amount == float(txn.amount))
        .where(Transaction.direction == txn.direction.value)
        .where(Transaction.source_type == "email")
        .where(Transaction.gmail_message_id != exclude_gmail_message_id)
    )
    exact = list(
        session.exec(base.where(Transaction.txn_date == txn.txn_date)).all()
    )
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    one_day = datetime.timedelta(days=1)
    window = list(
        session.exec(
            base.where(Transaction.txn_date >= txn.txn_date - one_day)
            .where(Transaction.txn_date <= txn.txn_date + one_day)
        ).all()
    )
    if len(window) == 1:
        return window[0]
    return None


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
    uid = user_id_for_account(account_id, session)
    one_day = datetime.timedelta(days=1)
    return session.exec(
        select(Transaction).where(
            Transaction.account_id == account_id,
            or_(
                col(Transaction.user_id) == uid,
                col(Transaction.user_id).is_(None),
            ),
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
    *,
    session: Session,
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
    if not existing.user_id:
        existing.user_id = user_id_for_account(txn.account_id, session)

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
    existing_run: PipelineRun | None = None,
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
        existing_run:      A pre-created PipelineRun row to adopt instead of creating a
                           new one.  Used by API route handlers that need to return a run
                           ID to the caller before the background work begins.  When
                           provided, this row is updated in place rather than a new row
                           being inserted.

    Returns:
        The PipelineRun row with final counts and status.

    Reconciliation (statement path only):
        When source_type="statement", before inserting a new row the writer checks
        whether a matching email-sourced row already exists
        (same account_id, exact amount, date ±1 day).  If found, that row is
        *upgraded* with the richer statement data instead of inserting a duplicate.
        This is the mechanism that prevents statement uploads from creating ghost
        duplicates of transactions the email scraper already captured.

    Email path (inverse):
        Before inserting email-sourced rows we check (1) statement/reconciled match
        (:func:`_find_statement_match_for_email`) and (2) a prior **transaction-alert** email
        row for the same spend (:func:`_find_prior_email_alert_match_for_pdf`).
        :func:`compute_content_hash` uses ``txn_date | raw_description | amount |
        account_id`` only — **not** ``ref_number`` — so PDF vs alert dedup relies on
        these paths, not Path A.  ``processed_emails`` is unchanged.
    """
    # Adopt a pre-created run row, or create a fresh audit-trail row.
    if existing_run is not None:
        run = existing_run
        run.source_key = source_key
        run.llm_model = llm_model
        run.status = "running"
        session.add(run)
        session.flush()
    else:
        run = PipelineRun(
            source_key=source_key,
            llm_model=llm_model,
            status="running",
        )
        session.add(run)
        session.flush()  # assigns run.id without committing
    assert run.id is not None  # flush guarantees this; tells mypy the id is set

    new_count = 0
    updated_count = 0
    reconciled_count = 0
    date_min: datetime.date | None = None
    date_max: datetime.date | None = None
    # Bank data changes surplus + EXPENSE_LIMIT progress — drop cached sim rows per user.
    affected_user_ids: set[str] = set()
    # Track content hashes for which we already inserted (Path C) in this batch.
    # A second canonical row with the same hash must not INSERT again (UNIQUE on
    # content_hash) — but we still merge classification from that row onto the
    # first row after flush, in case LLM filled fields only on the duplicate object.
    _batch_hashes: set[str] = set()

    for txn in txns:
        content_hash = compute_content_hash(txn)

        if content_hash in _batch_hashes:
            session.flush()
            row_user_id = user_id_for_account(txn.account_id, session)
            dup_existing = session.exec(
                select(Transaction).where(
                    Transaction.content_hash == content_hash,
                    Transaction.account_id == txn.account_id,
                    or_(
                        col(Transaction.user_id) == row_user_id,
                        col(Transaction.user_id).is_(None),
                    ),
                )
            ).first()
            if dup_existing is not None and _path_a_backfill_existing(
                session, dup_existing, txn, row_user_id
            ):
                updated_count += 1
                if row_user_id:
                    affected_user_ids.add(row_user_id)
            continue

        row_user_id = user_id_for_account(txn.account_id, session)

        # ── Path A: exact content_hash match ────────────────────────────────
        # This handles same-source dedup (re-running the same statement file,
        # or the same email coming in twice).  It's the fast path.
        # Allow legacy rows with NULL user_id (pre-migration) for same account/hash.
        existing = session.exec(
            select(Transaction).where(
                Transaction.content_hash == content_hash,
                Transaction.account_id == txn.account_id,
                or_(
                    col(Transaction.user_id) == row_user_id,
                    col(Transaction.user_id).is_(None),
                ),
            )
        ).first()

        if existing is not None:
            if _path_a_backfill_existing(session, existing, txn, row_user_id):
                updated_count += 1
                if row_user_id:
                    affected_user_ids.add(row_user_id)
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
                _upgrade_email_row(
                    email_match, txn, content_hash, run.id, session=session
                )
                session.add(email_match)
                reconciled_count += 1
                uid_r = email_match.user_id or user_id_for_account(account_id, session)
                if uid_r:
                    affected_user_ids.add(uid_r)

                if date_min is None or txn.txn_date < date_min:
                    date_min = txn.txn_date
                if date_max is None or txn.txn_date > date_max:
                    date_max = txn.txn_date
                continue

        # ── Path B2: email → skip if a statement row already captured this txn ───
        if source_type == "email":
            account_id = txn.account_id
            statement_dup = _find_statement_match_for_email(session, txn, account_id)
            if statement_dup is not None:
                logger.debug(
                    "Skipping email insert (statement already exists): "
                    "existing_id=%s account=%s amt=%s date=%s",
                    statement_dup.id,
                    account_id,
                    txn.amount,
                    txn.txn_date,
                )
                continue

            # ── Path B2b: PDF email → skip if a transaction-alert email row already exists ─
            # Same (date, amount, direction), different narration & different Gmail id.
            alert_dup = _find_prior_email_alert_match_for_pdf(
                session,
                txn,
                account_id,
                exclude_gmail_message_id=gmail_message_id,
            )
            if alert_dup is not None:
                logger.debug(
                    "Skipping email insert (prior email alert exists): "
                    "existing_id=%s account=%s amt=%s date=%s",
                    alert_dup.id,
                    account_id,
                    txn.amount,
                    txn.txn_date,
                )
                continue

        # ── Path C: brand-new row ────────────────────────────────────────────
        review_conf = compute_review_confidence(txn)
        if source_type == "email":
            reviewed = should_auto_review_email(review_conf)
        else:
            reviewed = True

        db_txn = Transaction(
            content_hash=content_hash,
            txn_date=txn.txn_date,
            account_id=txn.account_id,
            user_id=user_id_for_account(txn.account_id, session),
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
            spend_category=txn.spend_category.value if txn.spend_category else None,
            classification_source=(
                txn.classification_source.value if txn.classification_source else None
            ),
            raw_description=txn.raw_description,
            ref_number=txn.ref_number,
            closing_balance=float(txn.closing_balance) if txn.closing_balance else None,
            value_date=txn.value_date,
            notes=txn.notes,
            is_reviewed=reviewed,
            review_confidence=review_conf if source_type == "email" else None,
            pipeline_run_id=run.id,
            source_type=source_type,
            gmail_message_id=gmail_message_id,
        )
        session.add(db_txn)
        _batch_hashes.add(content_hash)
        new_count += 1
        ins_uid = db_txn.user_id or row_user_id
        if ins_uid:
            affected_user_ids.add(ins_uid)

        if date_min is None or txn.txn_date < date_min:
            date_min = txn.txn_date
        if date_max is None or txn.txn_date > date_max:
            date_max = txn.txn_date

    # Flush accumulated adds/updates so they are visible to the finalisation
    # queries below (e.g. goal-cache invalidation).  With autoflush=False on
    # SQLiteSerializingSession the session won't have written them yet.
    session.flush()

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
    if affected_user_ids:
        try:
            for uid in affected_user_ids:
                invalidate_goal_status_cache(session, uid)
            session.commit()
        except Exception:
            logger.exception(
                "goal_status_cache: invalidate after pipeline write failed for user_ids=%s",
                sorted(affected_user_ids),
            )
            session.rollback()
    return run
