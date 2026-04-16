"""
Match payment reminders to real transactions using example txn IDs.

Fingerprint: union of normalized counterparties from examples. If
description_match_anchors is set, ANY anchor must appear in raw_description or
ref_number (case-insensitive); amount bands are skipped. Otherwise, median
example amount ± tolerance plus optional reminder.amount floor applies.
Candidate pool: OUTFLOW rows included in analytics. Unlike dashboard expense
totals we **include CARD_PAYMENT** — paying a CC from savings is exactly what
bill reminders track; metrics still exclude CARD_PAYMENT to avoid double-count.
"""

from __future__ import annotations

import calendar
import datetime
import json
import statistics
from typing import Any

from sqlalchemy import or_
from sqlmodel import Session, col, select

from api.models import Reminder, Transaction
from api.reminder_anchor_derivation import decode_description_match_anchors
from api.services.query_helpers import _for_user

# Only drop true internal shuffles; keep CARD_PAYMENT for CC bill-pay matching.
_REMINDER_TXN_TYPE_EXCLUSIONS: tuple[str, ...] = ("SELF_TRANSFER",)

# Relative band around median example amount (e.g. 0.20 => ±20%).
AMOUNT_TOLERANCE = 0.20

# When reminder.amount is set, matched txn must be at least this fraction of it.
MIN_AMOUNT_VS_REMINDER = 0.85

# Cap on example IDs stored per reminder (API also enforces).
MAX_EXAMPLE_TRANSACTION_IDS = 5


def decode_example_transaction_ids(raw: str | None) -> list[int]:
    """Parse JSON array from DB column; invalid or empty -> []."""
    if raw is None or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for x in data:
        if isinstance(x, bool):
            continue
        if isinstance(x, int):
            out.append(x)
        elif isinstance(x, float) and x == int(x):
            out.append(int(x))
    return _dedupe_preserve_order(out)


def encode_example_transaction_ids(ids: list[int] | None) -> str | None:
    """Serialize for DB; None/empty -> NULL column."""
    if not ids:
        return None
    return json.dumps(ids)


def _dedupe_preserve_order(ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def month_date_range(ym: str) -> tuple[datetime.date, datetime.date]:
    """Inclusive (first_day, last_day) for calendar month 'YYYY-MM'."""
    parts = ym.split("-")
    if len(parts) != 2:
        raise ValueError("month must be YYYY-MM")
    year, month = int(parts[0]), int(parts[1])
    if month < 1 or month > 12:
        raise ValueError("invalid month")
    first = datetime.date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    last = datetime.date(year, month, last_day)
    return first, last


def _reminder_candidate_base(user_id: str):
    """OUTFLOW rows eligible for reminder matching (includes CC bill payments)."""
    q = select(Transaction).where(Transaction.direction == "OUTFLOW").where(
        col(Transaction.txn_type).is_(None)
        | col(Transaction.txn_type).not_in(_REMINDER_TXN_TYPE_EXCLUSIONS)
    )
    q = q.where(
        or_(
            col(Transaction.exclude_from_analytics).is_(None),
            col(Transaction.exclude_from_analytics).is_(False),
        )
    )
    return _for_user(q, user_id)


def _normalize_counterparty(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s.casefold() if s else None


def _median_amount(amounts: list[float]) -> float:
    if not amounts:
        return 0.0
    return float(statistics.median(amounts))


def compute_reminder_month_status(
    session: Session,
    reminder: Reminder,
    date_from: datetime.date,
    date_to: datetime.date,
) -> dict[str, Any]:
    """
    Single reminder: mapping health + whether any txn matches in [date_from, date_to].

    Returns keys: reminder_id, has_mapping, examples_stale, matched_this_month,
    matched_transactions, unmapped_reason (nullable str).
    """
    rid = reminder.id
    assert rid is not None
    stored_ids = decode_example_transaction_ids(reminder.example_transaction_ids)
    examples_stale = False

    if not stored_ids:
        return {
            "reminder_id": rid,
            "has_mapping": False,
            "examples_stale": False,
            "matched_this_month": False,
            "matched_transactions": [],
            "unmapped_reason": "no_examples",
        }

    example_rows: list[Transaction] = []
    for eid in stored_ids:
        t = session.get(Transaction, eid)
        if t is not None:
            example_rows.append(t)

    if len(example_rows) != len(stored_ids):
        examples_stale = True

    # Fingerprint only from valid OUTFLOW examples with non-empty counterparty.
    fp_txns = [
        t
        for t in example_rows
        if t.direction == "OUTFLOW"
        and _normalize_counterparty(t.counterparty) is not None
    ]

    if not fp_txns:
        return {
            "reminder_id": rid,
            "has_mapping": False,
            "examples_stale": examples_stale,
            "matched_this_month": False,
            "matched_transactions": [],
            "unmapped_reason": "no_valid_examples",
        }

    counterparty_keys: set[str] = set()
    for t in fp_txns:
        k = _normalize_counterparty(t.counterparty)
        if k:
            counterparty_keys.add(k)

    amounts = [float(t.amount) for t in fp_txns]
    med = _median_amount(amounts)
    low = med * (1.0 - AMOUNT_TOLERANCE)
    high = med * (1.0 + AMOUNT_TOLERANCE)

    anchors = decode_description_match_anchors(reminder.description_match_anchors)

    q = _reminder_candidate_base(reminder.user_id).where(
        Transaction.txn_date >= date_from,
        Transaction.txn_date <= date_to,
    )
    candidates = list(session.exec(q).all())

    matched: list[Transaction] = []
    for t in candidates:
        ck = _normalize_counterparty(t.counterparty)
        if ck is None or ck not in counterparty_keys:
            continue
        if anchors:
            blob = ((t.raw_description or "") + "\n" + (t.ref_number or "")).casefold()
            if not any(a.casefold() in blob for a in anchors):
                continue
            matched.append(t)
            continue
        amt = float(t.amount)
        if not (low <= amt <= high):
            continue
        if reminder.amount is not None and amt < MIN_AMOUNT_VS_REMINDER * float(
            reminder.amount
        ):
            continue
        matched.append(t)

    matched.sort(key=lambda x: x.txn_date)

    if matched:
        return {
            "reminder_id": rid,
            "has_mapping": True,
            "examples_stale": examples_stale,
            "matched_this_month": True,
            "matched_transactions": [_txn_brief(x) for x in matched],
            "unmapped_reason": None,
        }

    return {
        "reminder_id": rid,
        "has_mapping": True,
        "examples_stale": examples_stale,
        "matched_this_month": False,
        "matched_transactions": [],
        "unmapped_reason": "no_match_yet",
    }


def _txn_brief(t: Transaction) -> dict[str, Any]:
    assert t.id is not None
    return {
        "id": t.id,
        "txn_date": t.txn_date.isoformat(),
        "amount": round(float(t.amount), 2),
        "counterparty": t.counterparty,
    }


def compute_all_reminder_statuses(
    session: Session,
    user_id: str,
    month: str,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Status rows for all reminders of a user for calendar month `month` (YYYY-MM)."""
    date_from, date_to = month_date_range(month)
    q = select(Reminder).where(Reminder.user_id == user_id)
    if active_only:
        q = q.where(Reminder.is_active == True)  # noqa: E712
    q = q.order_by(col(Reminder.due_day_of_month), col(Reminder.name))
    reminders = list(session.exec(q).all())
    return [
        compute_reminder_month_status(session, r, date_from, date_to) for r in reminders
    ]
