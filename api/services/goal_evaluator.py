"""
Goal Progress Evaluator — Phase 4.5d + Track 3 cache

- ``EXPENSE_LIMIT``: spend vs cap from the transactions table (live, not surplus-sim).
- All other goal types: read from :mod:`api.services.goal_status_cache` (full multi-goal
  simulation on cache miss; fingerprint reuse on hit).
"""

from __future__ import annotations

import datetime
import logging

from sqlmodel import Session, func, select

from api.models import Goal, Transaction
from api.services.goal_status_cache import progress_from_cache_or_naive
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
    """Return a progress snapshot for the given goal (no categorical labels)."""
    logger.debug(
        "compute_progress: goal_id=%s goal_type=%s",
        goal.id,
        goal.goal_type,
    )
    if goal.goal_type == "EXPENSE_LIMIT":
        return _compute_expense_limit_progress(goal, session)
    return progress_from_cache_or_naive(goal, session)


def _compute_expense_limit_progress(goal: Goal, session: Session) -> dict:
    """Spend vs monthly/annual cap — orthogonal to surplus simulation."""
    current_value = _compute_expense_limit(goal, session)
    target = goal.target_amount or 0.0
    if target > 0:
        percentage = (current_value / target) * 100
    else:
        percentage = 0.0
    return {
        "current_value": round(current_value, 2),
        "target_amount": goal.target_amount,
        "percentage": round(percentage, 1),
        "status_data": None,
        "projected_completion_pct": None,
        "periods_met_pct": None,
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
