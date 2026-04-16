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

from sqlmodel import Session, func, select

from api.models import Goal, Transaction
from api.services.chart_metrics import (
    CHART_KEY_EXPENSE_NEED_WANT_STACK,
    expense_limit_sum_for_chart_key,
)
from api.services.query_helpers import _analytics_only, _date_where, _expense_where, _for_user

logger = logging.getLogger(__name__)


def expense_limit_spent_for_goal(
    goal: Goal,
    session: Session,
    date_from: datetime.date,
    date_to: datetime.date,
) -> float:
    """Sum spend for an EXPENSE_LIMIT goal over [date_from, date_to] (inclusive).

    Prefer ``chart_key``; else legacy ``linked_category``; if neither, use NEED+WANT
    total (same as the dashboard expense stack chart).
    """
    uid = goal.user_id
    if goal.chart_key:
        return expense_limit_sum_for_chart_key(
            session, goal.chart_key, date_from, date_to, uid
        )
    if goal.linked_category:
        base = _for_user(
            _expense_where(
                select(func.coalesce(func.sum(Transaction.amount), 0.0))
            ).where(Transaction.counterparty_category == goal.linked_category),
            uid,
        )
        q = _date_where(_analytics_only(base), date_from, date_to)
        return float(session.exec(q).one() or 0)
    return expense_limit_sum_for_chart_key(
        session, CHART_KEY_EXPENSE_NEED_WANT_STACK, date_from, date_to, uid
    )


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
    """Spend in the goal's evaluation window (month or calendar year for ANNUAL)."""
    today = datetime.date.today()
    cadence = (getattr(goal, "progress_cadence", None) or "MONTHLY").upper()
    if cadence == "ANNUAL":
        year_start = today.replace(month=1, day=1)
        return expense_limit_spent_for_goal(goal, session, year_start, today)
    month_start = today.replace(day=1)
    return expense_limit_spent_for_goal(goal, session, month_start, today)


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
