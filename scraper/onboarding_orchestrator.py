"""
Chunk-based onboarding backfill (Track 2 Phase 2b).

Wraps the same parse → classify → DB path as :mod:`scraper.orchestrator`, but:

  * Pulls Gmail history for **one** ``source_key`` (e.g. ``hdfc_savings``).
  * Processes **N messages per HTTP request** so the API stays responsive.
  * Persists queue + counters in :class:`~api.models.OnboardingState.backfill_progress_json`.
  * Pauses when “classification unknowns” for that source exceed a threshold.
  * **Statement-first:** annual/quarterly/monthly senders are drained before InstaAlerts;
    InstaAlert Gmail listing may be **deferred** until statements finish so the first POST
    does not paginate thousands of alerts and hit HTTP timeouts. After statements, alert
    IDs are filtered with :func:`scraper.gap_detector.filter_onboarding_alert_ids_after_statements`.

  * **HDFC Savings onboarding** intentionally skips the InstaAlert sweep (statements only);
    see ``ONBOARDING_SKIP_INSTAALERT_SOURCES``.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import and_, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, func, select

from api.models import OnboardingState, Transaction
from pipeline.models import CounterpartyCategory

from api.services.classifier_runtime import effective_onboarding_unknown_threshold
from api.services.email_import_flow_log import EmailImportFlowLog
from scraper.config_loader import BankSendersConfig, get_bank_senders_config
from scraper.email_router import _normalise_sender
from scraper.email_parsers import build_email_parser_registry
from scraper.gap_detector import filter_onboarding_alert_ids_after_statements
from scraper.gmail_client import GmailClient
from scraper.orchestrator import _get_processed_ids, _process_email, _record_email
from scraper.pdf_passwords import StatementPasswordRequired, is_statement_password_failure

logger = logging.getLogger(__name__)

# How many Gmail messages to drain per API call (tune for UX vs request time).
DEFAULT_CHUNK_SIZE = 10

# When unknown rows (per source_key) reach this count, pause for classification UI.
UNKNOWN_THRESHOLD = int(os.environ.get("ONBOARDING_UNKNOWN_THRESHOLD", "20"))

# Pipeline ``source_key`` values for which chunk onboarding imports **statement mail only**
# (no InstaAlert / per-transaction Gmail listing). Keeps the wizard fast; product can re-enable
# later per source once alert UX is ready.
ONBOARDING_SKIP_INSTAALERT_SOURCES: frozenset[str] = frozenset({"hdfc_savings"})

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


def _sender_cadence(cfg: dict[str, Any]) -> str:
    return str(cfg.get("expected_cadence") or "per_transaction").lower().strip()


# Senders with these cadences receive bulk statement PDFs (or similar), not per-txn alerts.
_STATEMENT_CADENCES = frozenset({"annual", "yearly", "quarterly", "monthly"})


def _statement_cadence_sort_key(cadence: str) -> tuple[int, str]:
    """Order statement jobs: annual-like first (coarse timeline), then quarterly, then monthly."""
    c = cadence.lower().strip()
    rank = {"annual": 0, "yearly": 0, "quarterly": 1, "monthly": 2}.get(c, 99)
    return (rank, c)


def _partition_senders_for_source(
    bank: BankSendersConfig, source_key: str
) -> tuple[list[str], list[str]]:
    """Split senders for ``source_key`` into (statement_senders, alert_senders).

    Statement senders are ordered **annual → quarterly → monthly** so FY / rare PDFs run
    before dense monthly runs (better priority if the HTTP budget is tight).

    Alert senders are everything else (typically ``per_transaction`` InstaAlerts).
    """
    senders = sender_emails_for_source_key(bank, source_key)
    stmt_rows: list[tuple[str, str]] = []
    alert: list[str] = []
    for s in senders:
        cfg = bank.get(s) or {}
        c = _sender_cadence(cfg)
        if c in _STATEMENT_CADENCES:
            stmt_rows.append((s, c))
        else:
            alert.append(s)
    stmt_rows.sort(key=lambda row: _statement_cadence_sort_key(row[1]))
    stmt = [s for s, _ in stmt_rows]
    return stmt, sorted(set(alert))


# Categories where the LLM is often wrong (P2P merchants, services mis-tagged as people).
_ONBOARDING_SENSITIVE_LLM_CATEGORIES: tuple[str, ...] = (
    CounterpartyCategory.FRIENDS_FAMILY.value,
    CounterpartyCategory.GIFTS_PERSONAL_TRANSFERS.value,
    CounterpartyCategory.MISCELLANEOUS.value,
)

# If the user already saved any row as ``USER_REVIEWED`` with the same normalized counterparty,
# we skip re-queuing *other* rows that only match the "LLM + sensitive category" rule — avoids
# asking them to confirm "Naseema Begum" again on every new UPI alert.
_PriorUserReviewedSameCp = aliased(Transaction)
_PRIOR_USER_REVIEWED_SAME_COUNTERPARTY = (
    select(1)
    .select_from(_PriorUserReviewedSameCp)
    .where(_PriorUserReviewedSameCp.user_id == Transaction.user_id)
    .where(_PriorUserReviewedSameCp.classification_source == "USER_REVIEWED")
    .where(col(_PriorUserReviewedSameCp.counterparty).is_not(None))
    .where(
        func.lower(func.trim(_PriorUserReviewedSameCp.counterparty))
        == func.lower(func.trim(Transaction.counterparty))
    )
).exists()

# Shared predicate: rows the onboarding classification queue shows for review.
#
# 1. **Automation gap** — missing counterparty or counterparty_category (rules + LLM did not finish).
# 2. **LLM high-risk labels** — both fields set, ``classification_source == LLM``, and category is
#    Friends & Family, Gifts & Personal Transfers, or Miscellaneous (needs human check), **unless**
#    another row for this user is already ``USER_REVIEWED`` with the same counterparty (trimmed,
#    case-insensitive). Rule-based rows with those categories are *not* re-queued.
#
# ``USER_REVIEWED`` rows are always excluded.
_CLASSIFICATION_UNKNOWN_PREDICATE = and_(
    or_(
        col(Transaction.classification_source).is_(None),
        col(Transaction.classification_source) != "USER_REVIEWED",
    ),
    or_(
        col(Transaction.counterparty).is_(None),
        col(Transaction.counterparty_category).is_(None),
        and_(
            col(Transaction.classification_source) == "LLM",
            col(Transaction.counterparty).is_not(None),
            col(Transaction.counterparty_category).is_not(None),
            col(Transaction.counterparty_category).in_(_ONBOARDING_SENSITIVE_LLM_CATEGORIES),
            ~_PRIOR_USER_REVIEWED_SAME_COUNTERPARTY,
        ),
    ),
)


def count_classification_unknowns(
    session: Session,
    *,
    user_id: str,
    source_key: str,
) -> int:
    """Count email-sourced rows for this source that need the onboarding review queue.

    Includes rows missing counterparty or category, plus LLM-labelled rows in sensitive
    categories (Friends & Family, Gifts & Personal Transfers, Miscellaneous) when this
    counterparty has not already been confirmed on another ``USER_REVIEWED`` row. Excludes
    ``USER_REVIEWED``.
    """
    q = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.source_statement == source_key)
        .where(Transaction.source_type == "email")
        .where(_CLASSIFICATION_UNKNOWN_PREDICATE)
    )
    return int(session.exec(q).one())


def count_all_classification_unknowns(session: Session, *, user_id: str) -> int:
    """Like :func:`count_classification_unknowns` but across every ``source_statement`` (email only)."""
    q = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.source_type == "email")
        .where(_CLASSIFICATION_UNKNOWN_PREDICATE)
    )
    return int(session.exec(q).one())


def list_classification_unknown_transactions(
    session: Session,
    *,
    user_id: str,
    source_key: str,
    limit: int = 200,
    offset: int = 0,
) -> list[Transaction]:
    """Return rows that match :func:`count_classification_unknowns`, oldest first (new imports at the end)."""
    return _list_classification_unknown_transactions_impl(
        session,
        user_id=user_id,
        source_key=source_key,
        limit=limit,
        offset=offset,
    )


def list_all_classification_unknown_transactions(
    session: Session,
    *,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[Transaction]:
    """Same criteria as :func:`count_all_classification_unknowns`, paged oldest-first for the wizard."""
    return _list_classification_unknown_transactions_impl(
        session,
        user_id=user_id,
        source_key=None,
        limit=limit,
        offset=offset,
    )


def _list_classification_unknown_transactions_impl(
    session: Session,
    *,
    user_id: str,
    source_key: str | None,
    limit: int,
    offset: int,
) -> list[Transaction]:
    lim = max(1, min(int(limit), 500))
    off = max(0, int(offset))
    q = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .where(Transaction.source_type == "email")
        .where(_CLASSIFICATION_UNKNOWN_PREDICATE)
    )
    if source_key is not None:
        q = q.where(Transaction.source_statement == source_key)
    q = q.order_by(col(Transaction.txn_date).asc(), col(Transaction.id).asc()).offset(off).limit(lim)
    return list(session.exec(q).all())


@dataclass
class CollectedQueue:
    """Gmail IDs split for statement-first onboarding import."""

    statement_ids: list[str]
    alert_items_full: list[dict[str, str]]  # each: id, received_at (ISO)
    # True when this source has statement-cadence senders configured (annual/monthly/…).
    # Used with gap detection: treat statements as source-of-truth when present.
    had_statement_ids_at_init: bool
    # When True, InstaAlert Gmail list/search was **not** run yet — it runs after statement
    # emails are processed so a single POST does not paginate thousands of alerts + timeout.
    defer_alert_fetch: bool

    @property
    def total_planned(self) -> int:
        return len(self.statement_ids) + len(self.alert_items_full)


def _gather_alert_message_items(
    client: GmailClient,
    bank: BankSendersConfig,
    source_key: str,
    *,
    after: datetime.date,
    before: datetime.date,
    session: Session,
    exclude_message_ids: set[str],
    import_flow_log: EmailImportFlowLog | None = None,
) -> list[dict[str, str]]:
    """Run Gmail search for InstaAlert / per-transaction senders only (heavy pagination)."""
    if source_key in ONBOARDING_SKIP_INSTAALERT_SOURCES:
        if import_flow_log:
            import_flow_log.write(
                "gmail_alert_search_skipped",
                f"source_key={source_key} reason=onboarding_inst_alert_disabled",
            )
        return []
    _, alert_senders = _partition_senders_for_source(bank, source_key)
    if not alert_senders:
        return []

    already_done = _get_processed_ids(session)
    after_s = after.strftime("%Y/%m/%d")
    before_s = before.strftime("%Y/%m/%d")
    alert_msgs: dict[str, Any] = {}

    if import_flow_log:
        import_flow_log.write(
            "gmail_alert_search_begin",
            f"source_key={source_key} alert_senders={len(alert_senders)} after={after_s} before={before_s}",
        )

    for raw_sender in alert_senders:
        query = f"from:{raw_sender} after:{after_s} before:{before_s}"
        batch = client.search_messages(
            query,
            paginate=True,
            max_results_per_page=100,
            max_total=None,
        )
        if import_flow_log:
            import_flow_log.write(
                "gmail_search_done",
                f"phase={'alert'!r} sender={raw_sender!r} messages_in_date_range={len(batch)} "
                f"query={query!r}",
            )
        for m in batch:
            alert_msgs[m.id] = m

    alert_pending = sorted(alert_msgs.values(), key=lambda m: m.received_at)
    return [
        {"id": m.id, "received_at": m.received_at.isoformat()}
        for m in alert_pending
        if m.id not in already_done
        and m.id not in exclude_message_ids
    ]


def _collect_pending_queue(
    client: GmailClient,
    bank: BankSendersConfig,
    source_key: str,
    *,
    after: datetime.date,
    before: datetime.date,
    session: Session,
    import_flow_log: EmailImportFlowLog | None = None,
) -> CollectedQueue:
    """Gather Gmail message IDs: statement senders first, then optionally alert senders.

    **Statement senders** (annual / quarterly / monthly): searched immediately, oldest first.

    **Alert senders** (InstaAlerts): when at least one statement sender exists for this
    ``source_key``, alert listing is **deferred** until statement emails are drained.
    That avoids one HTTP request paginating thousands of alert IDs (browser/API timeout).

    ``had_statement_ids_at_init`` is True when statement-cadence senders are configured,
    matching :func:`scraper.gap_detector.filter_onboarding_alert_ids_after_statements`.
    """
    stmt_senders, alert_senders = _partition_senders_for_source(bank, source_key)
    if source_key in ONBOARDING_SKIP_INSTAALERT_SOURCES:
        alert_senders = []
    all_senders = sender_emails_for_source_key(bank, source_key)
    if not all_senders:
        raise ValueError(
            f"No configured bank sender maps to source_key={source_key!r}. "
            "Check scraper account mappings."
        )

    defer_alerts = len(stmt_senders) > 0
    had_stmt_phase_config = len(stmt_senders) > 0

    already_done = _get_processed_ids(session)
    after_s = after.strftime("%Y/%m/%d")
    before_s = before.strftime("%Y/%m/%d")

    stmt_msgs: dict[str, Any] = {}
    alert_msgs: dict[str, Any] = {}

    if import_flow_log:
        import_flow_log.write(
            "gmail_search_plan",
            f"source_key={source_key} statement_senders={len(stmt_senders)} "
            f"alert_senders={len(alert_senders)} defer_alert_listing={defer_alerts} "
            f"after={after_s} before={before_s}",
        )

    for raw_sender in stmt_senders:
        query = f"from:{raw_sender} after:{after_s} before:{before_s}"
        batch = client.search_messages(
            query,
            paginate=True,
            max_results_per_page=100,
            max_total=None,
        )
        if import_flow_log:
            import_flow_log.write(
                "gmail_search_done",
                f"phase='statement' sender={raw_sender!r} messages_in_date_range={len(batch)} "
                f"query={query!r}",
            )
        for m in batch:
            stmt_msgs[m.id] = m

    if not defer_alerts:
        for raw_sender in alert_senders:
            query = f"from:{raw_sender} after:{after_s} before:{before_s}"
            batch = client.search_messages(
                query,
                paginate=True,
                max_results_per_page=100,
                max_total=None,
            )
            if import_flow_log:
                import_flow_log.write(
                    "gmail_search_done",
                    f"phase='alert' sender={raw_sender!r} messages_in_date_range={len(batch)} "
                    f"query={query!r}",
                )
            for m in batch:
                alert_msgs[m.id] = m

    stmt_pending = sorted(stmt_msgs.values(), key=lambda m: m.received_at)
    alert_pending = sorted(alert_msgs.values(), key=lambda m: m.received_at)

    stmt_ids = [m.id for m in stmt_pending if m.id not in already_done]
    stmt_set = set(stmt_ids)
    alert_items_full = [
        {"id": m.id, "received_at": m.received_at.isoformat()}
        for m in alert_pending
        if m.id not in already_done and m.id not in stmt_set
    ]

    if import_flow_log:
        n_stmt = len(stmt_ids)
        n_alert = len(alert_items_full)
        import_flow_log.write(
            "gmail_dedupe",
            f"statement_pending={n_stmt} alert_pending_unfiltered={n_alert} "
            f"defer_alert_listing={defer_alerts} "
            f"(skipped_already_in_ledger={len(stmt_msgs) + len(alert_msgs) - n_stmt - n_alert})",
        )

    return CollectedQueue(
        statement_ids=stmt_ids,
        alert_items_full=alert_items_full,
        had_statement_ids_at_init=had_stmt_phase_config,
        defer_alert_fetch=defer_alerts,
    )


def _public_slice(src: dict[str, Any]) -> dict[str, Any]:
    """Strip underscore-prefixed internal keys before returning JSON to clients."""
    return {k: v for k, v in src.items() if not str(k).startswith("_")}


def _has_any_pending(src: dict[str, Any]) -> bool:
    if src.get("_pending_statement_ids"):
        return True
    # Statements drained but InstaAlert IDs not listed yet (deferred fetch).
    if src.get("_defer_alert_fetch") and not src.get("_alerts_transitioned"):
        return True
    if not src.get("_alerts_transitioned") and (src.get("_alert_items_full") or []):
        return True
    if src.get("_pending_alert_ids"):
        return True
    return False


def _load_backfill_progress_from_db(
    session: Session,
    user_id: str,
    source_key: str,
) -> dict[str, Any]:
    """Return the persisted onboarding backfill blob for ``source_key``, or ``{}``.

    Used to detect when another HTTP request has already committed queue state while
    this request still holds a stale ``existing_progress`` snapshot from request start
    (overlapping POSTs).
    """
    row = session.exec(
        select(OnboardingState).where(OnboardingState.user_id == user_id)
    ).first()
    if row is None:
        return {}
    # Same request may have loaded this row already; another worker may have committed
    # since then — force a fresh read for adoption.
    session.refresh(row)
    try:
        all_bf = json.loads(row.backfill_progress_json or "{}")
    except json.JSONDecodeError:
        return {}
    return dict(all_bf.get(source_key) or {})


def _ensure_alert_queue_ready(
    session: Session,
    user_id: str,
    source_key: str,
    bank: BankSendersConfig,
    src_state: dict[str, Any],
    *,
    gmail_client: GmailClient | None = None,
    after: datetime.date | None = None,
    before: datetime.date | None = None,
    import_flow_log: EmailImportFlowLog | None = None,
    progress_commit_hook: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    if src_state.get("_alerts_transitioned"):
        return
    # Wipe any deferred InstaAlert payload for sources we import as statements-only, so a
    # half-finished JSON blob from an older build cannot resurrect a giant alert search.
    if source_key in ONBOARDING_SKIP_INSTAALERT_SOURCES:
        src_state["_alert_items_full"] = []
        src_state["_defer_alert_fetch"] = False
    full = list(src_state.get("_alert_items_full") or [])

    if src_state.get("_defer_alert_fetch") and not full:
        if gmail_client is None or after is None or before is None:
            logger.warning(
                "Deferred InstaAlert listing missing gmail_client or date window — "
                "skipping alert fetch for source_key=%s",
                source_key,
            )
            full = []
            src_state["_defer_alert_fetch"] = False
        elif source_key in ONBOARDING_SKIP_INSTAALERT_SOURCES:
            full = []
            src_state["_alert_items_full"] = full
            src_state["_defer_alert_fetch"] = False
            if import_flow_log:
                import_flow_log.write(
                    "gmail_alert_search_skipped",
                    f"source_key={source_key} reason=onboarding_inst_alert_disabled",
                )
        else:
            exclude_ids = set(src_state.get("_statement_id_set_at_init") or [])
            # InstaAlert listing can paginate for a long time. When the API passes
            # ``progress_commit_hook``, flush a snapshot first so GET /progress does not
            # look "stuck" on the last statement counts while Gmail search runs.
            if progress_commit_hook is not None:
                src_state["status"] = "processing_alerts"
                src_state["current_phase"] = "listing_alerts"
                progress_commit_hook(dict(src_state))
            full = _gather_alert_message_items(
                gmail_client,
                bank,
                source_key,
                after=after,
                before=before,
                session=session,
                exclude_message_ids=exclude_ids,
                import_flow_log=import_flow_log,
            )
            src_state["_alert_items_full"] = full
            src_state["_defer_alert_fetch"] = False

    had = bool(src_state.get("_had_statement_ids_at_init"))
    filtered_ids = filter_onboarding_alert_ids_after_statements(
        session,
        user_id,
        source_key,
        bank,
        full,
        had_statement_ids_at_init=had,
    )
    src_state["_pending_alert_ids"] = filtered_ids
    src_state["_alerts_transitioned"] = True

    stmt_planned = int(src_state.get("_statement_total_at_init") or 0)
    src_state["emails_found"] = stmt_planned + len(filtered_ids)
    src_state["_initial_pending_total"] = src_state["emails_found"]

    if import_flow_log:
        import_flow_log.write(
            "gmail_alert_queue_after_gaps",
            f"alert_ids_after_gap_filter={len(filtered_ids)} (had_statement_phase={had})",
        )


def _active_drain_queue(src_state: dict[str, Any]) -> tuple[list[str], str]:
    """Return (ids_to_drain_head_slice, public_status_for_slice)."""
    stmt = list(src_state.get("_pending_statement_ids") or [])
    if stmt:
        return stmt, "processing_statements"
    alerts = list(src_state.get("_pending_alert_ids") or [])
    return alerts, "processing_alerts"


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
    resume_after_password: bool = False,
    resume_from_pause: bool = False,
    unknown_threshold: int | None = None,
    progress_callback: ProgressCallback | None = None,
    import_flow_log: EmailImportFlowLog | None = None,
    progress_commit_hook: Callable[[dict[str, Any]], None] | None = None,
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
        resume_after_password: When status is ``needs_password``, pass True after the user
            saved PAN/DOB/account ingredients (or full env-style passwords) so the same
            Gmail message can be retried.
        resume_from_pause: When status is ``paused``, pass True to clear the pause
            flag and continue chunk processing on the next call.
        unknown_threshold: Override env ``ONBOARDING_UNKNOWN_THRESHOLD``.
        progress_callback: Optional hook invoked after each email (tests / logging).
        import_flow_log: When provided (onboarding HTTP handler), append diagnostics to
            ``data/logs/email-import.log``.
        progress_commit_hook: Optional callback invoked with a shallow copy of ``src_state``
            so the HTTP layer can ``commit`` mid-run (e.g. before heavy InstaAlert Gmail search).

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

    after_date = after
    before_date = before
    if after_date is None:
        after_date = datetime.date.today() - datetime.timedelta(days=365 * _DEFAULT_LOOKBACK_YEARS)
    if before_date is None:
        before_date = _today_plus_one()

    src_state: dict[str, Any] = dict(existing_progress or {})
    status = str(src_state.get("status") or "idle")

    def _resolve_backfill_window() -> tuple[datetime.date, datetime.date]:
        """Use persisted ``_after`` / ``_before`` when resuming mid-run."""
        ad, bd = after_date, before_date
        ra = src_state.get("_after")
        rb = src_state.get("_before")
        if isinstance(ra, str):
            try:
                ad = datetime.date.fromisoformat(ra[:10])
            except ValueError:
                pass
        if isinstance(rb, str):
            try:
                bd = datetime.date.fromisoformat(rb[:10])
            except ValueError:
                pass
        return ad, bd

    def _ensure_alerts() -> None:
        ad, bd = _resolve_backfill_window()
        _ensure_alert_queue_ready(
            session,
            user_id,
            source_key,
            bank,
            src_state,
            gmail_client=gmail_client,
            after=ad,
            before=bd,
            import_flow_log=import_flow_log,
            progress_commit_hook=progress_commit_hook,
        )

    if import_flow_log:
        import_flow_log.write(
            "backfill_step",
            f"incoming_status={status!r} chunk_size={chunk_size} resume_after_classification={resume_after_classification} resume_after_password={resume_after_password} resume_from_pause={resume_from_pause}",
        )

    if status == "paused" and resume_from_pause:
        src_state = resume_backfill_state(src_state)
        status = str(src_state.get("status") or "processing")

    if status == "paused":
        if import_flow_log:
            import_flow_log.write("backfill_exit", "still paused — client must pass resume_from_pause=true")
        return OnboardingBackfillResult(
            progress={
                **src_state,
                "source": source_key,
                "status": "paused",
                "error_message": src_state.get("error_message"),
                "message": "Set resume_from_pause=true on the next POST to continue.",
            }
        )

    if status == "complete" and not _has_any_pending(src_state):
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
        if import_flow_log:
            import_flow_log.write("backfill_exit", f"already complete unknowns_pending={unknowns_refresh}")
        return OnboardingBackfillResult(progress=merged)

    if status == "needs_classification" and not resume_after_classification:
        unknowns = int(src_state.get("unknowns_pending") or 0)
        if import_flow_log:
            import_flow_log.write(
                "backfill_exit",
                f"waiting for classification UI unknowns_pending={unknowns} (pass resume_after_classification to continue)",
            )
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

    if status == "needs_password" and not resume_after_password:
        if import_flow_log:
            import_flow_log.write(
                "backfill_exit",
                "waiting for PDF password ingredients (pass resume_after_password after saving secrets)",
            )
        return OnboardingBackfillResult(
            progress={
                **src_state,
                "source": source_key,
                "status": "needs_password",
                "unknowns_pending": count_classification_unknowns(
                    session, user_id=user_id, source_key=source_key
                ),
                "error_message": src_state.get("error_message"),
                "message": "Save PAN/DOB/account fragments or env passwords, then pass resume_after_password=true.",
            }
        )

    # Transition out of PDF-password gate (retry same Gmail IDs).
    if status == "needs_password" and resume_after_password:
        src_state.pop("password_failure_message_id", None)
        src_state.pop("password_parser_key", None)
        src_state["error_message"] = None
        active_qp, pub_qp = _active_drain_queue(src_state)
        if active_qp:
            src_state["status"] = pub_qp
            src_state["current_phase"] = "statements" if pub_qp == "processing_statements" else "alerts"
        status = str(src_state.get("status") or "processing")

    # Transition out of classification gate.
    if status == "needs_classification" and resume_after_classification:
        src_state["status"] = "processing_statements"
        stmt0 = list(src_state.get("_pending_statement_ids") or [])
        if not stmt0:
            _ensure_alerts()
            al0 = list(src_state.get("_pending_alert_ids") or [])
            src_state["status"] = "processing_alerts" if al0 else "processing"

    # Initialise queue on first chunk (do not rebuild after a finished run — caller clears JSON).
    need_init = not _has_any_pending(src_state) and status in ("idle", "error")

    # Concurrency guard (shared-sender / overlapping POST): ``existing_progress`` is a
    # snapshot from the start of this HTTP request. Another in-flight request may have
    # already committed an active queue to ``OnboardingState`` while this one was still
    # idle in memory. Re-read the DB before running ``_collect_pending_queue`` (expensive
    # Gmail search) and adopt the remote queue.  We cannot use a naive
    # ``if status in ("processing_*",) return`` here — that would block legitimate
    # sequential chunk POSTs, which also report ``processing_statements`` / ``alerts``.
    if need_init:
        remote = _load_backfill_progress_from_db(session, user_id, source_key)
        r_status = str(remote.get("status") or "idle")
        if _has_any_pending(remote) and r_status in (
            "processing_statements",
            "processing_alerts",
            "processing",
        ):
            src_state = dict(remote)
            status = str(src_state.get("status") or "idle")
            need_init = False
            if import_flow_log:
                import_flow_log.write(
                    "backfill_concurrency",
                    "adopted in-flight queue from DB — skipping duplicate Gmail search",
                )

    if need_init:
        try:
            q = _collect_pending_queue(
                gmail_client,
                bank,
                source_key,
                after=after_date,
                before=before_date,
                session=session,
                import_flow_log=import_flow_log,
            )
        except Exception as exc:
            logger.exception("Failed to list Gmail messages for %s", source_key)
            if import_flow_log:
                import_flow_log.write("error", f"gmail list/build queue failed: {exc!r}")
            return OnboardingBackfillResult(
                progress={
                    "source": source_key,
                    "status": "error",
                    "emails_found": 0,
                    "emails_processed": 0,
                    "transactions_parsed": 0,
                    "unknowns_pending": 0,
                    "error_message": str(exc),
                    "current_phase": None,
                }
            )

        stmt_total = len(q.statement_ids)
        emails_found = stmt_total + (0 if q.defer_alert_fetch else len(q.alert_items_full))
        if import_flow_log:
            import_flow_log.write(
                "gmail_queue_built",
                f"statements={stmt_total} alerts_unfiltered={len(q.alert_items_full)} "
                f"defer_alert_listing={q.defer_alert_fetch} total_planned_now={emails_found}",
            )
        src_state.update(
            {
                "status": "processing_statements" if q.statement_ids else "processing_alerts",
                "current_phase": "statements" if q.statement_ids else "alerts",
                "emails_found": emails_found,
                "emails_processed": 0,
                "transactions_parsed": 0,
                "unknowns_pending": 0,
                "error_message": None,
                "_pending_statement_ids": list(q.statement_ids),
                "_alert_items_full": list(q.alert_items_full),
                "_had_statement_ids_at_init": q.had_statement_ids_at_init,
                "_defer_alert_fetch": q.defer_alert_fetch,
                "_statement_total_at_init": stmt_total,
                "_statement_id_set_at_init": list(q.statement_ids),
                "_alerts_transitioned": False,
                "_pending_alert_ids": [],
                "_after": after_date.isoformat(),
                "_before": before_date.isoformat(),
                "_initial_pending_total": emails_found,
            }
        )
        if not q.statement_ids:
            _ensure_alerts()
            al = list(src_state.get("_pending_alert_ids") or [])
            src_state["status"] = "processing_alerts" if al else "processing"
            src_state["current_phase"] = "alerts" if al else None

    # Prepare alert queue once statement tier is drained.
    if not (src_state.get("_pending_statement_ids") or []):
        _ensure_alerts()

    active_q, pub_status = _active_drain_queue(src_state)
    initial_total = int(src_state.get("_initial_pending_total") or len(active_q))

    if not active_q:
        unknowns = count_classification_unknowns(session, user_id=user_id, source_key=source_key)
        done = int(src_state.get("emails_processed") or 0)
        src_state.update(
            {
                "source": source_key,
                "status": "complete",
                "emails_found": max(initial_total, done),
                "emails_processed": done,
                "transactions_parsed": src_state.get("transactions_parsed", 0),
                "unknowns_pending": unknowns,
                "error_message": None,
                "current_phase": None,
            }
        )
        for k in (
            "_pending_statement_ids",
            "_pending_alert_ids",
            "_alert_items_full",
            "_alerts_transitioned",
            "_had_statement_ids_at_init",
            "_defer_alert_fetch",
            "_statement_total_at_init",
            "_statement_id_set_at_init",
        ):
            src_state.pop(k, None)
        if import_flow_log:
            import_flow_log.write(
                "backfill_exit",
                f"queue empty — status=complete unknowns_pending={unknowns}",
            )
        return OnboardingBackfillResult(progress=src_state)

    chunk_n = max(1, chunk_size)
    chunk = active_q[:chunk_n]
    rest = active_q[chunk_n:]

    tx_total = int(src_state.get("transactions_parsed") or 0)
    emails_done = int(src_state.get("emails_processed") or 0)

    src_state["status"] = pub_status
    src_state["current_phase"] = "statements" if pub_status == "processing_statements" else "alerts"

    already_done = _get_processed_ids(session)

    for i, msg_id in enumerate(chunk):
        if msg_id in already_done:
            logger.debug("Skipping already-processed email %s during backfill chunk", msg_id)
            emails_done += 1
            continue

        try:
            if import_flow_log:
                import_flow_log.write(
                    "chunk_item",
                    f"fetch id={msg_id} (email {emails_done + 1} of this chunk, {len(chunk)} in batch) phase={pub_status!r}",
                )
            msg = gmail_client.fetch_message_by_id(msg_id)
            status_result, txn_count = _process_email(
                msg,
                client=gmail_client,
                session=session,
                parser_registry=parser_registry,
                user_id=user_id,
                import_flow_log=import_flow_log,
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
                    "status": pub_status,
                    "emails_found": initial_total,
                    "emails_processed": emails_done,
                    "transactions_parsed": tx_total,
                    "current_phase": src_state.get("current_phase"),
                }
            )
            if progress_callback:
                progress_callback(slice_pub)

        except Exception as exc:
            if is_statement_password_failure(exc):
                session.rollback()
                remainder_chunk = chunk[i + 1 :]
                rebuilt = [msg_id] + remainder_chunk + list(rest)
                if pub_status == "processing_statements":
                    src_state["_pending_statement_ids"] = rebuilt
                else:
                    src_state["_pending_alert_ids"] = rebuilt
                p_key = getattr(exc, "parser_key", None)
                if not p_key and isinstance(exc, StatementPasswordRequired):
                    p_key = exc.parser_key
                err_txt = str(exc).strip() or "PDF password missing or incorrect."
                src_state.update(
                    {
                        "source": source_key,
                        "status": "needs_password",
                        "emails_found": initial_total,
                        "emails_processed": emails_done,
                        "transactions_parsed": tx_total,
                        "unknowns_pending": count_classification_unknowns(
                            session, user_id=user_id, source_key=source_key
                        ),
                        "password_failure_message_id": msg_id,
                        "password_parser_key": p_key,
                        "error_message": err_txt,
                        "current_phase": src_state.get("current_phase"),
                        "message": "Save credentials and POST with resume_after_password=true.",
                    }
                )
                if import_flow_log:
                    import_flow_log.write(
                        "needs_password",
                        f"message_id={msg_id} parser_key={p_key!r} detail={err_txt!r}",
                    )
                return OnboardingBackfillResult(progress=src_state)

            logger.exception("Onboarding backfill failed on message %s", msg_id)
            # The failed operation may have left the SQLAlchemy session in a
            # dirty state (e.g. IntegrityError → PendingRollbackError).  We
            # must rollback before any further DB work on this session.
            session.rollback()
            if import_flow_log:
                import_flow_log.write("error", f"message_id={msg_id} {exc!r}")
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
            if pub_status == "processing_statements":
                src_state["_pending_statement_ids"] = rest
            else:
                src_state["_pending_alert_ids"] = rest
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
                }
            )
            return OnboardingBackfillResult(progress=src_state)

    if pub_status == "processing_statements":
        src_state["_pending_statement_ids"] = rest
    else:
        src_state["_pending_alert_ids"] = rest

    stmt_rest = list(src_state.get("_pending_statement_ids") or [])
    if not stmt_rest:
        _ensure_alerts()
    alert_rest = list(src_state.get("_pending_alert_ids") or [])

    src_state["emails_processed"] = emails_done
    src_state["transactions_parsed"] = tx_total
    if src_state.get("_alerts_transitioned"):
        pass  # ``_ensure_alerts`` already set ``emails_found`` / ``_initial_pending_total``
    else:
        src_state["emails_found"] = initial_total

    unknowns = count_classification_unknowns(session, user_id=user_id, source_key=source_key)
    src_state["unknowns_pending"] = unknowns

    if unknowns >= thresh:
        src_state["status"] = "needs_classification"
        if not stmt_rest and not alert_rest:
            for k in (
                "_pending_statement_ids",
                "_pending_alert_ids",
                "_alert_items_full",
                "_alerts_transitioned",
                "_had_statement_ids_at_init",
                "_defer_alert_fetch",
                "_statement_total_at_init",
                "_statement_id_set_at_init",
            ):
                src_state.pop(k, None)
    elif not stmt_rest and not alert_rest:
        src_state["status"] = "complete"
        src_state["current_phase"] = None
        for k in (
            "_pending_statement_ids",
            "_pending_alert_ids",
            "_alert_items_full",
            "_alerts_transitioned",
            "_had_statement_ids_at_init",
            "_defer_alert_fetch",
            "_statement_total_at_init",
            "_statement_id_set_at_init",
        ):
            src_state.pop(k, None)
    else:
        _next_ids, pub2 = _active_drain_queue(src_state)
        src_state["status"] = pub2
        src_state["current_phase"] = (
            "statements"
            if pub2 == "processing_statements"
            else ("alerts" if pub2 == "processing_alerts" else None)
        )

    src_state["source"] = source_key
    src_state["error_message"] = None
    if import_flow_log:
        import_flow_log.write(
            "backfill_step_done",
            f"status={src_state.get('status')!r} emails_processed={src_state.get('emails_processed')} "
            f"txns={src_state.get('transactions_parsed')} unknowns={unknowns} "
            f"stmt_remaining={len(stmt_rest)} alert_remaining={len(alert_rest)}",
        )
    return OnboardingBackfillResult(progress=src_state)


def pause_backfill_state(src: dict[str, Any]) -> dict[str, Any]:
    """Mark a single-source progress blob as paused (internal helper)."""
    out = dict(src or {})
    if out.get("status") in ("processing", "processing_statements", "processing_alerts"):
        out["status"] = "paused"
    return out


def resume_backfill_state(src: dict[str, Any]) -> dict[str, Any]:
    """Clear paused flag so the next POST processes chunks again."""
    out = dict(src or {})
    if out.get("status") == "paused":
        stmt = list(out.get("_pending_statement_ids") or [])
        if stmt:
            out["status"] = "processing_statements"
        elif out.get("_pending_alert_ids"):
            out["status"] = "processing_alerts"
        else:
            out["status"] = "processing"
    return out
