"""
Recurring Transaction Detection Service — Phase 4.5c (rev 2)

Identifies transactions that happen at predictable intervals and upserts them
into the `recurring_patterns` table.

Algorithm (per counterparty + direction group):
  1. Fetch all transactions grouped by (counterparty, direction).
     INCOME_OTHER is excluded — those are one-off credits, not salary/recurring.
  2. Deduplicate: collapse transactions within 3 days of each other into one
     "occurrence" (takes the max amount). Handles duplicate rent payments, etc.
  3. Segment detection: split the timeline wherever a gap exceeds 2.5× the
     rough median interval. Use only the LATEST contiguous streak. This way a
     job change (111-day gap) or landlord change doesn't poison the variance
     calculation — we detect the *current* recurring pattern, not the history.
  4. Require ≥ 3 occurrences in the latest streak.
  5. Variance check — adaptive threshold:
       • ≥ 15 occurrences in the streak → allow up to 40% variance (strong evidence)
       • Default → 25% variance threshold
  6. Classify frequency: ~7d=WEEKLY, ~30d=MONTHLY, ~90d=QUARTERLY, ~365d=YEARLY
  7. Upsert into recurring_patterns (match on counterparty + direction + frequency)
  8. Mark patterns inactive if overdue beyond a grace period

False-positive control:
  - Minimum 3 occurrences in latest streak required
  - Variance thresholds above filter irregular spending (e.g. Swiggy)
  - User can explicitly dismiss patterns via PATCH /api/recurring/{id}
"""

from __future__ import annotations

import datetime
import logging
from statistics import median, stdev

from sqlmodel import Session, col, select

from api.models import RecurringPattern, Transaction
from api.services.account_user_map import user_id_for_account

logger = logging.getLogger(__name__)

# Minimum occurrences (after dedup) in the latest streak to call it recurring.
_MIN_OCCURRENCES = 3

# Default variance ratio limit: std_dev / median_interval must be below this.
_MAX_VARIANCE_RATIO_DEFAULT = 0.25

# Relaxed variance ratio for high-confidence groups (many occurrences).
_MAX_VARIANCE_RATIO_RELAXED = 0.40
_RELAXED_THRESHOLD_COUNT = 15  # occurrences needed to use the relaxed ratio

# Gap multiplier: if a gap between two consecutive transactions exceeds
# (rough_median * this), we treat it as a "break" and start a new streak.
_GAP_BREAK_MULTIPLIER = 2.5

# Dedup window: transactions within this many days of each other for the
# same (counterparty, direction) are treated as the same occurrence.
_DEDUP_WINDOW_DAYS = 3

# Frequency bucket boundaries in days (inclusive ranges).
_FREQ_RANGES: list[tuple[str, int, int]] = [
    ("WEEKLY",    5,   9),
    ("MONTHLY",  25,  35),
    ("QUARTERLY", 80, 100),
    ("YEARLY",  340, 380),
]


def detect_and_upsert(session: Session) -> dict[str, int]:
    """Run full recurring detection and upsert results into the DB.

    Returns a dict with counts: { "created": N, "updated": N, "deactivated": N }
    """
    logger.info("Recurring detector: starting detection pass")

    # Exclude transaction types that are internal cash flows or one-off credits —
    # these shouldn't influence what counts as a recurring income/expense pattern.
    #   INCOME_OTHER   — cashback, ad-hoc credits; not a predictable income stream
    #   CARD_PAYMENT   — paying your own credit card bill (internal transfer)
    #   SELF_TRANSFER  — moving money between own accounts (internal transfer)
    _EXCLUDED_TXN_TYPES = {"INCOME_OTHER", "CARD_PAYMENT", "SELF_TRANSFER"}
    txns = session.exec(
        select(Transaction)
        .where(Transaction.counterparty.is_not(None))  # type: ignore[union-attr]
        .where(Transaction.txn_type.not_in(list(_EXCLUDED_TXN_TYPES)))  # type: ignore[union-attr]
        .order_by(col(Transaction.txn_date))
    ).all()

    # Group by (user_id, counterparty, direction) for OUTFLOW.
    # For INFLOW, also include txn_type in the key — a single person/entity can
    # send salary, dividends, family transfers, and one-off payments all under the
    # same counterparty name. Without the txn_type split, monthly salary gets
    # buried in frequent small transfers and fails the variance check.
    # user_id comes from Transaction.user_id when set, else account→user map (legacy rows).
    groups: dict[tuple[str, str, str, str | None], list[Transaction]] = {}
    for txn in txns:
        rid = (txn.user_id or "").strip()
        uid = rid or user_id_for_account(txn.account_id)
        txn_type_key = txn.txn_type if txn.direction == "INFLOW" else None
        key = (uid, txn.counterparty or "", txn.direction, txn_type_key)
        groups.setdefault(key, []).append(txn)

    created = 0
    updated = 0

    for (pattern_user_id, counterparty, direction, _txn_type_key), group_txns in groups.items():
        group_txns.sort(key=lambda t: t.txn_date)

        raw_dates = [t.txn_date for t in group_txns]
        raw_amounts = [float(t.amount) for t in group_txns]

        # ── Step 1: Deduplicate ────────────────────────────────────────────────
        # Collapse bursts of payments within _DEDUP_WINDOW_DAYS into one
        # "occurrence" (duplicate rent payment, double-charged subscription, etc.)
        dedup_dates, dedup_amounts = _dedup_occurrences(raw_dates, raw_amounts)

        if len(dedup_dates) < _MIN_OCCURRENCES:
            continue

        # ── Step 2: Segment — use the latest contiguous streak ─────────────────
        # A "break" in the timeline (gap > 2.5× rough median) means the pattern
        # changed (new employer, new landlord, cancelled subscription that restarted).
        # We want to detect the *current* recurring pattern, not the full history.
        streak_dates, streak_amounts = _latest_streak(dedup_dates, dedup_amounts)

        if len(streak_dates) < _MIN_OCCURRENCES:
            continue

        # ── Step 3: Compute intervals and variance ─────────────────────────────
        intervals = [
            (streak_dates[i + 1] - streak_dates[i]).days
            for i in range(len(streak_dates) - 1)
        ]

        if not intervals:
            continue

        med_interval = median(intervals)
        if med_interval == 0:
            continue

        std = stdev(intervals) if len(intervals) > 1 else 0.0

        # Adaptive variance threshold: more occurrences = more evidence = leniency
        max_variance = (
            _MAX_VARIANCE_RATIO_RELAXED
            if len(streak_dates) >= _RELAXED_THRESHOLD_COUNT
            else _MAX_VARIANCE_RATIO_DEFAULT
        )

        if std / med_interval > max_variance:
            continue

        frequency = _classify_frequency(med_interval)
        if frequency is None:
            continue

        # ── Step 4: Compute pattern stats ──────────────────────────────────────
        expected_amount = median(streak_amounts)
        amount_tolerance = stdev(streak_amounts) if len(streak_amounts) > 1 else 0.0

        last_date = streak_dates[-1]
        next_expected = last_date + datetime.timedelta(days=round(med_interval))

        # Active if seen within 2× the expected interval from today
        days_since_last = (datetime.date.today() - last_date).days
        is_active = days_since_last <= med_interval * 2

        day_of_month = last_date.day if frequency == "MONTHLY" else None

        # Use the most recent transaction's counterparty_category as the label
        counterparty_category = group_txns[-1].counterparty_category

        # ── Step 5: Upsert ─────────────────────────────────────────────────────
        existing = session.exec(
            select(RecurringPattern)
            .where(RecurringPattern.user_id == pattern_user_id)
            .where(RecurringPattern.counterparty == counterparty)
            .where(RecurringPattern.direction == direction)
            .where(RecurringPattern.frequency == frequency)
        ).first()

        now = datetime.datetime.now(datetime.UTC)

        if existing:
            existing.user_id = pattern_user_id
            existing.counterparty_category = counterparty_category
            existing.expected_amount = expected_amount
            existing.amount_tolerance = amount_tolerance
            existing.last_seen_date = last_date
            existing.next_expected_date = next_expected
            existing.is_active = is_active
            existing.match_count = len(streak_dates)
            existing.total_amount = sum(streak_amounts)
            existing.day_of_month = day_of_month
            existing.updated_at = now
            session.add(existing)
            updated += 1
        else:
            pattern = RecurringPattern(
                user_id=pattern_user_id,
                counterparty=counterparty,
                counterparty_category=counterparty_category,
                direction=direction,
                expected_amount=expected_amount,
                amount_tolerance=amount_tolerance,
                frequency=frequency,
                day_of_month=day_of_month,
                last_seen_date=last_date,
                next_expected_date=next_expected,
                is_active=is_active,
                match_count=len(streak_dates),
                total_amount=sum(streak_amounts),
            )
            session.add(pattern)
            created += 1

    # ── Step 6: Deactivate overdue patterns ────────────────────────────────────
    # Flush newly created / updated patterns so the SELECT below sees them
    # (autoflush=False on SQLiteSerializingSession means session.exec() won't
    # flush automatically).
    session.flush()
    all_patterns = session.exec(select(RecurringPattern)).all()
    deactivated = 0
    for pat in all_patterns:
        if pat.is_active and pat.next_expected_date:
            days_past_due = (datetime.date.today() - pat.next_expected_date).days
            grace = _interval_days_for_frequency(pat.frequency) * 0.5
            if days_past_due > grace:
                pat.is_active = False
                pat.updated_at = datetime.datetime.now(datetime.UTC)
                session.add(pat)
                deactivated += 1

    session.commit()

    logger.info(
        "Recurring detector: created=%d updated=%d deactivated=%d",
        created, updated, deactivated,
    )
    return {"created": created, "updated": updated, "deactivated": deactivated}


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _dedup_occurrences(
    dates: list[datetime.date],
    amounts: list[float],
    window_days: int = _DEDUP_WINDOW_DAYS,
) -> tuple[list[datetime.date], list[float]]:
    """Collapse consecutive transactions within `window_days` into one occurrence.

    When two payments land very close together (e.g. rent paid twice in 3 days),
    they represent a single "billing event", not two separate occurrences.
    We keep the maximum amount (in case one is a partial payment).
    """
    if not dates:
        return [], []

    merged_dates: list[datetime.date] = [dates[0]]
    merged_amounts: list[float] = [amounts[0]]

    for i in range(1, len(dates)):
        gap = (dates[i] - merged_dates[-1]).days
        if gap <= window_days:
            # Same occurrence — take the larger amount
            merged_amounts[-1] = max(merged_amounts[-1], amounts[i])
        else:
            merged_dates.append(dates[i])
            merged_amounts.append(amounts[i])

    return merged_dates, merged_amounts


def _latest_streak(
    dates: list[datetime.date],
    amounts: list[float],
) -> tuple[list[datetime.date], list[float]]:
    """Return the most recent contiguous streak, splitting on large gaps.

    A "large gap" is defined as > _GAP_BREAK_MULTIPLIER × the rough median
    interval (estimated from all intervals that are below the overall median,
    to avoid skewing from the very gap we're trying to detect).

    Example: salary paid monthly Oct 2022–May 2025, then 111-day gap (job change),
    then resumes Sep 2025. This function returns only the Sep 2025–present streak,
    so the variance check sees a clean monthly pattern instead of a blown-up one.
    """
    if len(dates) < 2:
        return dates, amounts

    all_intervals = [
        (dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)
    ]

    # Estimate the "normal" interval excluding obvious large gaps.
    # Use the lower half of intervals as a robust estimate.
    sorted_intervals = sorted(all_intervals)
    lower_half = sorted_intervals[: max(1, len(sorted_intervals) // 2)]
    rough_median = median(lower_half)

    gap_threshold = rough_median * _GAP_BREAK_MULTIPLIER

    # Walk backwards to find where the latest streak starts.
    # The streak begins after the most recent gap that exceeds the threshold.
    streak_start_idx = 0
    for i in range(len(all_intervals) - 1, -1, -1):
        if all_intervals[i] > gap_threshold:
            streak_start_idx = i + 1
            break

    return dates[streak_start_idx:], amounts[streak_start_idx:]


def _classify_frequency(median_interval_days: float) -> str | None:
    """Map a median interval in days to a frequency label."""
    for label, lo, hi in _FREQ_RANGES:
        if lo <= median_interval_days <= hi:
            return label
    return None


def _interval_days_for_frequency(frequency: str) -> int:
    """Return the canonical interval days for a given frequency label."""
    mapping = {"WEEKLY": 7, "MONTHLY": 30, "QUARTERLY": 91, "YEARLY": 365}
    return mapping.get(frequency, 30)
