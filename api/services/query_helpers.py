"""
Shared SQLModel query fragments for transaction analytics.

Used by :mod:`api.routes.metrics`, :mod:`api.services.chart_metrics`,
:mod:`api.services.goal_evaluator`, and :mod:`api.services.surplus_calculator`
so we do not duplicate exclusion rules (CARD_PAYMENT, SELF_TRANSFER, etc.).
"""

from __future__ import annotations

import datetime

from sqlalchemy import or_
from sqlmodel import col

from api.models import Transaction


def _for_user(q, user_id: str):
    """Restrict a query to rows owned by this Arth user (session username)."""
    return q.where(Transaction.user_id == user_id)


# txn_types excluded from income totals (these inflows aren't real income)
_INCOME_EXCLUSIONS: tuple[str, ...] = ("SELF_TRANSFER",)

# txn_types excluded from expense totals (these outflows aren't real spending)
_EXPENSE_EXCLUSIONS: tuple[str, ...] = ("CARD_PAYMENT", "SELF_TRANSFER")


def _current_month_range() -> tuple[datetime.date, datetime.date]:
    """Return (first day of current month, today)."""
    today = datetime.date.today()
    return today.replace(day=1), today


def _income_where(q):
    """
    Narrow a query to real income transactions.

    The IS NULL branch matters: SQLite evaluates `NULL NOT IN (...)` as NULL
    (not TRUE), which would silently drop unclassified transactions. We want
    to INCLUDE them — if it's an INFLOW and not explicitly SELF_TRANSFER,
    we treat it as income.
    """
    return q.where(Transaction.direction == "INFLOW").where(
        col(Transaction.txn_type).is_(None)
        | col(Transaction.txn_type).not_in(_INCOME_EXCLUSIONS)
    )


def _expense_where(q):
    """Narrow a query to real expense transactions (same NULL-safe logic)."""
    return q.where(Transaction.direction == "OUTFLOW").where(
        col(Transaction.txn_type).is_(None)
        | col(Transaction.txn_type).not_in(_EXPENSE_EXCLUSIONS)
    )


def _date_where(q, date_from: datetime.date, date_to: datetime.date):
    """Add an inclusive date range filter."""
    return q.where(
        Transaction.txn_date >= date_from,
        Transaction.txn_date <= date_to,
    )


def _analytics_only(q):
    """Exclude rows the user marked as ignored for analytics."""
    return q.where(
        or_(
            col(Transaction.exclude_from_analytics).is_(None),
            col(Transaction.exclude_from_analytics).is_(False),
        )
    )


def _generate_month_labels(n: int) -> list[str]:
    """
    Generate a list of 'YYYY-MM' strings for the last n months (oldest first).

    Example: if today is March 2026 and n=3 → ['2026-01', '2026-02', '2026-03']
    """
    today = datetime.date.today()
    base = today.year * 12 + (today.month - 1)  # 0-based month count
    labels = []
    for i in range(n - 1, -1, -1):
        total = base - i
        year, mo = divmod(total, 12)
        labels.append(f"{year:04d}-{mo + 1:02d}")
    return labels


def _last_day_of_calendar_month(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


def _month_start_end(ym: str) -> tuple[datetime.date, datetime.date]:
    y, m = map(int, ym.split("-"))
    start = datetime.date(y, m, 1)
    end = _last_day_of_calendar_month(y, m)
    today = datetime.date.today()
    if end > today:
        end = today
    return start, end
