"""
Chunk-based onboarding backfill (Track 2 Phase 2b).

Wraps the same parse → classify → DB path as :mod:`scraper.orchestrator`, but:

  * Pulls Gmail history for **one** ``source_key`` (e.g. ``hdfc_savings``).
  * Processes **N messages per HTTP request** so the API stays responsive.
  * Persists queue + counters in :class:`~api.models.OnboardingState.backfill_progress_json`.
  * Pauses when “classification unknowns” for that source exceed a threshold.

The frontend polls ``GET /api/onboarding/backfill/{source}/progress`` and calls
``POST /api/onboarding/backfill/{source}`` repeatedly to advance chunks (or
after inline classification — Phase 3 wires the classify endpoint).
"""

from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import and_, or_
from sqlmodel import Session, col, func, select

from api.models import Transaction

from api.services.classifier_runtime import effective_onboarding_unknown_threshold
from scraper.config_loader import BankSendersConfig, get_bank_senders_config
from scraper.email_router import _normalise_sender
from scraper.email_parsers import build_email_parser_registry
from scraper.gmail_client import GmailClient
from scraper.orchestrator import _get_processed_ids, _process_email, _record_email

logger = logging.getLogger(__name__)

# How many Gmail messages to drain per API call (tune for UX vs request time).
DEFAULT_CHUNK_SIZE = 10

# When unknown rows (per source_key) reach this count, pause for classification UI.
UNKNOWN_THRESHOLD = int(os.environ.get("ONBOARDING_UNKNOWN_THRESHOLD", "20"))

# Default historical sweep — wide window; callers can override with after/before.
_DEFAULT_LOOKBACK_YEARS = 15


ProgressCallback = Callable[[dict[str, Any]], None]


def _today_plus_one() -> datetime.date:
    """Gmail ``before:`` is exclusive — use tomorrow UTC date as a practical upper bound."""
    return datetime.date.today() + datetime.timedelta(days=1)


def sender_emails_for_source_key(bank: BankSendersConfig, source_key: str) -> list[str]:
    """Return configured sender addresses that feed a given ``source_key``."""
    found: list[str] = []
    for sender_email, cfg in bank.items():
        for acct in cfg.get("accounts", {}).values():
            if acct.get("source_key") == source_key:
                found.append(sender_email)
                break
    return sorted(set(found))


def account_ids_for_source_key(bank: BankSendersConfig, source_key: str) -> list[str]:
    """Return bank ``account_id`` strings associated with ``source_key``."""
    ids: set[str] = set()
    for cfg in bank.values():
        for acct in cfg.get("accounts", {}).values():
            if acct.get("source_key") == source_key:
                ids.add(str(acct["account_id"]))
    return sorted(ids)


def count_classification_unknowns(
    session: Session,
    *,
    user_id: str,
    source_key: str,
) -> int:
    """Count email-sourced rows for this source that still need automation fields.

    Mirrors the pipeline notion of “LLM work remaining”: missing ``txn_type``,
    counterparty taxonomy, UPI subtype (when channel is UPI), or OUTFLOW spend tag.
    """
    q = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.source_statement == source_key)
        .where(Transaction.source_type == "email")
        .where(
            or_(
                col(Transaction.txn_type).is_(None),
                col(Transaction.counterparty).is_(None),
                col(Transaction.counterparty_category).is_(None),
                and_(col(Transaction.channel) == "UPI", col(Transaction.upi_type).is_(None)),
                and_(
                    col(Transaction.direction) == "OUTFLOW",
                    col(Transaction.spend_category).is_(None),
                ),
            )
        )
    )
    return int(session.exec(q).one())


def list_classification_unknown_transactions(
    session: Session,
    *,
    user_id: str,
    source_key: str,
    limit: int = 200,
) -> list[Transaction]:
    """Return recent rows that still match :func:`count_classification_unknowns` (for batch UI)."""
    lim = max(1, min(int(limit), 500))
    q = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.source_statement == source_key)
        .where(Transaction.source_type == "email")
        .where(
            or_(
                col(Transaction.txn_type).is_(None),
                col(Transaction.counterparty).is_(None),
                col(Transaction.counterparty_category).is_(None),
                and_(col(Transaction.channel) == "UPI", col(Transaction.upi_type).is_(None)),
                and_(
                    col(Transaction.direction) == "OUTFLOW",
                    col(Transaction.spend_category).is_(None),
                ),
            )
        )
        .order_by(col(Transaction.txn_date).desc(), col(Transaction.id).desc())
        .limit(lim)
    )
    return list(session.exec(q).all())


def _collect_pending_messages(
    client: GmailClient,
    bank: BankSendersConfig,
    source_key: str,
    *,
    after: datetime.date,
    before: datetime.date,
    session: Session,
) -> list[str]:
    """List Gmail message IDs (oldest first) for all senders mapped to ``source_key``.

    Skips IDs already present in ``processed_emails`` (dedupe ledger).
    """
    senders = sender_emails_for_source_key(bank, source_key)
    if not senders:
        raise ValueError(
            f"No configured bank sender maps to source_key={source_key!r}. "
            "Check scraper account mappings."
        )

    already_done = _get_processed_ids(session)
    after_s = after.strftime("%Y/%m/%d")
    before_s = before.strftime("%Y/%m/%d")

    gathered: dict[str, Any] = {}
    for raw_sender in senders:
        query = f"from:{raw_sender} after:{after_s} before:{before_s}"
        batch = client.search_messages(
            query,
            paginate=True,
            max_results_per_page=100,
            max_total=None,
        )
        for m in batch:
            gathered[m.id] = m

    pending_msgs = sorted(gathered.values(), key=lambda m: m.received_at)
    pending_ids = [m.id for m in pending_msgs if m.id not in already_done]
    return pending_ids


def _public_slice(src: dict[str, Any]) -> dict[str, Any]:
    """Strip underscore-prefixed internal keys before returning JSON to clients."""
    return {k: v for k, v in src.items() if not k.startswith("_")}


@dataclass
class OnboardingBackfillResult:
    """Return payload for one chunk step."""

    progress: dict[str, Any]

    @property
    def public_progress(self) -> dict[str, Any]:
        return _public_slice(self.progress)


def run_onboarding_backfill(
    *,
    session: Session,
    user_id: str,
    source_key: str,
    gmail_client: GmailClient,
    existing_progress: dict[str, Any],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    after: datetime.date | None = None,
    before: datetime.date | None = None,
    resume_after_classification: bool = False,
    resume_from_pause: bool = False,
    unknown_threshold: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> OnboardingBackfillResult:
    """Advance onboarding backfill by **one chunk** (up to ``chunk_size`` emails).

    Args:
        session: Active SQLModel session (caller commits after persisting state).
        user_id: Authenticated Arth username (same as Gmail scraper mapping owner).
        source_key: Pipeline ``source_key`` e.g. ``hdfc_savings``.
        gmail_client: Authenticated Gmail client.
        existing_progress: Parsed ``backfill_progress_json[source_key]`` dict or ``{}``.
        chunk_size: Max messages to process this call.
        after / before: Gmail date window (inclusive ``after``, exclusive ``before``).
            Defaults to ~15 years through tomorrow when initializing a fresh run.
        resume_after_classification: When current status is ``needs_classification``,
            pass True to continue processing remaining queued IDs after the user
            fixed merchant rules (Phase 3 will set this from the classify endpoint).
        resume_from_pause: When status is ``paused``, pass True to clear the pause
            flag and continue chunk processing on the next call.
        unknown_threshold: Override env ``ONBOARDING_UNKNOWN_THRESHOLD``.
        progress_callback: Optional hook invoked after each email (tests / logging).

    Returns:
        :class:`OnboardingBackfillResult` with updated progress dict (includes ``_``
        internal keys — strip with :meth:`OnboardingBackfillResult.public_progress`).
    """
    if unknown_threshold is not None:
        thresh = unknown_threshold
    else:
        thresh = effective_onboarding_unknown_threshold(session, user_id)
    bank = get_bank_senders_config(session, user_id)
    parser_registry = build_email_parser_registry(bank)

    src_state: dict[str, Any] = dict(existing_progress or {})
    status = str(src_state.get("status") or "idle")

    if status == "paused" and resume_from_pause:
        src_state = resume_backfill_state(src_state)
        status = str(src_state.get("status") or "processing")

    if status == "paused":
        return OnboardingBackfillResult(
            progress={
                **src_state,
                "source": source_key,
                "status": "paused",
                "error_message": src_state.get("error_message"),
                "message": "Set resume_from_pause=true on the next POST to continue.",
            }
        )

    pending_early = list(src_state.get("_pending_message_ids") or [])
    if status == "complete" and not pending_early:
        unknowns_refresh = count_classification_unknowns(
            session, user_id=user_id, source_key=source_key
        )
        merged = {
            **src_state,
            "source": source_key,
            "status": "complete",
            "unknowns_pending": unknowns_refresh,
            "error_message": None,
        }
        return OnboardingBackfillResult(progress=merged)

    if status == "needs_classification" and not resume_after_classification:
        unknowns = int(src_state.get("unknowns_pending") or 0)
        return OnboardingBackfillResult(
            progress={
                **src_state,
                "source": source_key,
                "status": "needs_classification",
                "unknowns_pending": unknowns,
                "error_message": src_state.get("error_message"),
                "message": "Pass resume_after_classification=true after resolving unknowns.",
            }
        )

    # Transition out of classification gate.
    if status == "needs_classification" and resume_after_classification:
        src_state["status"] = "processing"
        status = "processing"

    after_date = after
    before_date = before
    if after_date is None:
        after_date = datetime.date.today() - datetime.timedelta(days=365 * _DEFAULT_LOOKBACK_YEARS)
    if before_date is None:
        before_date = _today_plus_one()

    pending_ids: list[str] = list(src_state.get("_pending_message_ids") or [])

    # Initialise queue on first chunk (do not rebuild after a finished run — caller clears JSON).
    # Only "idle" / "error" may fetch a fresh Gmail ID list so we never clobber an in-flight queue.
    need_init = not pending_ids and status in ("idle", "error")
    if need_init:
        try:
            pending_ids = _collect_pending_messages(
                gmail_client,
                bank,
                source_key,
                after=after_date,
                before=before_date,
                session=session,
            )
        except Exception as exc:
            logger.exception("Failed to list Gmail messages for %s", source_key)
            return OnboardingBackfillResult(
                progress={
                    "source": source_key,
                    "status": "error",
                    "emails_found": 0,
                    "emails_processed": 0,
                    "transactions_parsed": 0,
                    "unknowns_pending": 0,
                    "error_message": str(exc),
                }
            )

        emails_found = len(pending_ids)
        src_state.update(
            {
                "status": "processing",
                "emails_found": emails_found,
                "emails_processed": 0,
                "transactions_parsed": 0,
                "unknowns_pending": 0,
                "error_message": None,
                "_pending_message_ids": pending_ids,
                "_after": after_date.isoformat(),
                "_before": before_date.isoformat(),
                "_initial_pending_total": emails_found,
            }
        )

    pending_ids = list(src_state.get("_pending_message_ids") or [])
    initial_total = int(src_state.get("_initial_pending_total") or len(pending_ids))

    if not pending_ids:
        unknowns = count_classification_unknowns(session, user_id=user_id, source_key=source_key)
        src_state.update(
            {
                "source": source_key,
                "status": "complete",
                "emails_found": initial_total,
                "emails_processed": src_state.get("emails_processed", 0),
                "transactions_parsed": src_state.get("transactions_parsed", 0),
                "unknowns_pending": unknowns,
                "error_message": None,
            }
        )
        src_state.pop("_pending_message_ids", None)
        return OnboardingBackfillResult(progress=src_state)

    # Drain up to chunk_size messages.
    chunk_n = max(1, chunk_size)
    chunk = pending_ids[:chunk_n]
    rest = pending_ids[chunk_n:]

    tx_total = int(src_state.get("transactions_parsed") or 0)
    emails_done = int(src_state.get("emails_processed") or 0)

    for msg_id in chunk:
        try:
            msg = gmail_client.fetch_message_by_id(msg_id)
            status_result, txn_count = _process_email(
                msg,
                client=gmail_client,
                session=session,
                parser_registry=parser_registry,
                user_id=user_id,
            )
            sender_norm = _normalise_sender(msg.sender)
            _record_email(
                session,
                msg,
                sender=sender_norm,
                status=status_result,
                txn_count=txn_count,
            )
            emails_done += 1
            if status_result == "processed":
                tx_total += txn_count

            slice_pub = _public_slice(
                {
                    **src_state,
                    "source": source_key,
                    "status": "processing",
                    "emails_found": initial_total,
                    "emails_processed": emails_done,
                    "transactions_parsed": tx_total,
                }
            )
            if progress_callback:
                progress_callback(slice_pub)

        except Exception as exc:
            logger.exception("Onboarding backfill failed on message %s", msg_id)
            err_msg = str(exc)
            try:
                msg = gmail_client.fetch_message_by_id(msg_id)
                sender_norm = _normalise_sender(msg.sender)
                _record_email(
                    session,
                    msg,
                    sender=sender_norm,
                    status="failed",
                    error_message=err_msg,
                )
            except Exception:
                logger.warning("Could not record failed ProcessedEmail for %s", msg_id)

            emails_done += 1
            src_state.update(
                {
                    "status": "error",
                    "emails_found": initial_total,
                    "emails_processed": emails_done,
                    "transactions_parsed": tx_total,
                    "unknowns_pending": count_classification_unknowns(
                        session, user_id=user_id, source_key=source_key
                    ),
                    "error_message": err_msg,
                    "_pending_message_ids": rest,
                }
            )
            return OnboardingBackfillResult(progress=src_state)

    src_state["_pending_message_ids"] = rest
    src_state["emails_processed"] = emails_done
    src_state["transactions_parsed"] = tx_total
    src_state["emails_found"] = initial_total

    unknowns = count_classification_unknowns(session, user_id=user_id, source_key=source_key)
    src_state["unknowns_pending"] = unknowns

    if unknowns >= thresh:
        src_state["status"] = "needs_classification"
        if not rest:
            src_state.pop("_pending_message_ids", None)
    elif not rest:
        src_state["status"] = "complete"
        src_state.pop("_pending_message_ids", None)
    else:
        src_state["status"] = "processing"

    src_state["source"] = source_key
    src_state["error_message"] = None
    return OnboardingBackfillResult(progress=src_state)


def pause_backfill_state(src: dict[str, Any]) -> dict[str, Any]:
    """Mark a single-source progress blob as paused (internal helper)."""
    out = dict(src or {})
    if out.get("status") == "processing":
        out["status"] = "paused"
    return out


def resume_backfill_state(src: dict[str, Any]) -> dict[str, Any]:
    """Clear paused flag so the next POST processes chunks again."""
    out = dict(src or {})
    if out.get("status") == "paused":
        out["status"] = "processing"
    return out
