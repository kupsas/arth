"""
Goal Progress Evaluator — Phase 4.5d

Computes current progress and derives status for a Goal record.

EXPENSE_LIMIT goals are auto-computed from the transactions table (current
month's actual spend in the linked category vs the target limit).

All other goal types rely on `current_value` — a number the user manually
enters via PATCH /api/goals/{id}.

Status derivation:
  - EXPENSE_LIMIT: ON_TRACK → AT_RISK (≥85% of limit) → BEHIND (≥100%)
  - Other goals:   ACHIEVED (≥100%) → ON_TRACK (on pace) → AT_RISK → BEHIND
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import or_
from sqlmodel import Session, col, func, select

from api.models import Goal, Transaction

logger = logging.getLogger(__name__)


def compute_progress(goal: Goal, session: Session) -> dict:
    """Return a progress snapshot for the given goal.

    Returns:
        {
            "current_value": float,
            "target_amount": float | None,
            "percentage": float,   # 0-100+
            "status": str,         # ON_TRACK | AT_RISK | BEHIND | ACHIEVED | PAUSED
        }
    """
    if goal.status == "PAUSED":
        return {
            "current_value": goal.current_value or 0.0,
            "target_amount": goal.target_amount,
            "percentage": 0.0,
            "status": "PAUSED",
        }

    if goal.goal_type == "EXPENSE_LIMIT":
        current_value = _compute_expense_limit(goal, session)
    else:
        current_value = goal.current_value or 0.0

    target = goal.target_amount or 0.0

    if target > 0:
        percentage = (current_value / target) * 100
    else:
        percentage = 0.0

    status = _derive_status(goal, current_value, target)

    return {
        "current_value": round(current_value, 2),
        "target_amount": target,
        "percentage": round(percentage, 1),
        "status": status,
    }


def _compute_expense_limit(goal: Goal, session: Session) -> float:
    """Sum OUTFLOW transactions in goal's linked_category for the current month.

    The limit resets every calendar month, so we always compare against
    the current month's total spend.
    """
    today = datetime.date.today()
    month_start = today.replace(day=1)

    query = (
        select(func.sum(Transaction.amount))
        .where(Transaction.direction == "OUTFLOW")
        .where(Transaction.txn_date >= month_start)
        .where(Transaction.txn_date <= today)
        # Exclude pass-through payments that don't represent real spending
        .where(Transaction.txn_type.not_in(["SELF_TRANSFER", "CARD_PAYMENT"]))  # type: ignore[union-attr]
        .where(
            or_(
                col(Transaction.exclude_from_analytics).is_(None),
                col(Transaction.exclude_from_analytics).is_(False),
            )
        )
    )

    if goal.linked_category:
        query = query.where(
            Transaction.counterparty_category == goal.linked_category
        )

    result = session.exec(query).first()
    return float(result or 0.0)


def _derive_status(goal: Goal, current_value: float, target_amount: float) -> str:
    """Determine goal status from progress and deadline.

    Rules:
      EXPENSE_LIMIT: lower is better.
        - BEHIND if current ≥ target (already blown the limit)
        - AT_RISK if current ≥ 85% of target
        - ON_TRACK otherwise

      All other types: higher is better.
        - ACHIEVED if current ≥ target
        - If there's a deadline: compare progress-so-far vs expected-progress-by-now
        - Without a deadline: ON_TRACK unless obviously stalled
    """
    if target_amount <= 0:
        return goal.status  # no target set; preserve whatever status was manually set

    if goal.goal_type == "EXPENSE_LIMIT":
        ratio = current_value / target_amount
        if ratio >= 1.0:
            return "BEHIND"
        if ratio >= 0.85:
            return "AT_RISK"
        return "ON_TRACK"

    # Higher-is-better goals
    if current_value >= target_amount:
        return "ACHIEVED"

    ratio = current_value / target_amount

    if goal.target_date:
        today = datetime.date.today()

        if today > goal.target_date:
            return "BEHIND"  # deadline passed without achieving

        # How far through the timeline are we?
        # Use the goal's creation date as the start of the timeline.
        timeline_start = goal.created_at.date() if goal.created_at else today
        total_days = (goal.target_date - timeline_start).days
        elapsed_days = (today - timeline_start).days

        if total_days > 0:
            time_progress = elapsed_days / total_days
            # If you're at 70% of value with 80% of time gone → AT_RISK
            if ratio >= time_progress * 0.9:
                return "ON_TRACK"
            if ratio >= time_progress * 0.7:
                return "AT_RISK"
            return "BEHIND"

    return "ON_TRACK"
