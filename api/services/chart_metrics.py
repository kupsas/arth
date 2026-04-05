"""
Dashboard chart keys and SQL predicates — single source of truth for metrics + goals.

Each ``chart_key`` ties a Goal row to the same filters the dashboard chart uses.
"""

from __future__ import annotations

from typing import Final

from sqlmodel import Session, col, func, select

from api.models import Transaction

# ───────────────────────────────────────────────────────────────────────────
# Chart keys (stable strings; keep in sync with dashboard)
# ───────────────────────────────────────────────────────────────────────────

CHART_KEY_EXPENSE_NEED_WANT_STACK: Final = "expense_need_want_stack"
CHART_KEY_INVESTMENT_NET: Final = "investment_net"

# Prefix for category mini-charts: "category:swiggy_food", etc.
CATEGORY_CHART_PREFIX: Final = "category:"

# Series strings accepted by /api/metrics/category-trend (and bar-drilldown).
CATEGORY_TREND_SERIES: Final[frozenset[str]] = frozenset(
    {
        "swiggy_instamart",
        "swiggy_food",
        "food_and_dining",
        "gifts",
        "shopping",
        "transport",
        "travel",
    }
)

KNOWN_CHART_KEYS: Final[frozenset[str]] = frozenset(
    {CHART_KEY_EXPENSE_NEED_WANT_STACK, CHART_KEY_INVESTMENT_NET}
    | {f"{CATEGORY_CHART_PREFIX}{s}" for s in CATEGORY_TREND_SERIES}
)


def category_trend_condition(series: str):
    """SQLAlchemy boolean: transactions included in the category-trend series (expense-side)."""
    fd = "Food & Dining"
    if series == "swiggy_instamart":
        return Transaction.counterparty == "Swiggy Instamart"
    if series == "swiggy_food":
        return Transaction.counterparty == "Swiggy Food"
    if series == "food_and_dining":
        return (Transaction.counterparty_category == fd) | (
            Transaction.counterparty == "Swiggy Dineout"
        )
    if series == "gifts":
        return Transaction.counterparty_category == "Gifts & Personal Transfers"
    if series == "shopping":
        return Transaction.counterparty_category == "Shopping & E-commerce"
    if series == "transport":
        return Transaction.counterparty_category == "Transport & Fuel"
    if series == "travel":
        return Transaction.counterparty_category == "Travel & Stay"
    raise ValueError(f"unknown series: {series}")


def parse_category_chart_key(chart_key: str) -> str | None:
    """If chart_key is category:<series>, return series; else None."""
    if not chart_key.startswith(CATEGORY_CHART_PREFIX):
        return None
    s = chart_key[len(CATEGORY_CHART_PREFIX) :]
    if s not in CATEGORY_TREND_SERIES:
        return None
    return s


def is_known_chart_key(key: str | None) -> bool:
    return key is not None and key in KNOWN_CHART_KEYS


def validate_chart_key_for_goal(goal_type: str, chart_key: str | None) -> None:
    """Raise ValueError if chart_key is invalid for this goal_type."""
    if chart_key is None:
        return
    if not is_known_chart_key(chart_key):
        raise ValueError(f"Unknown chart_key: {chart_key!r}")
    if goal_type == "EXPENSE_LIMIT":
        if chart_key == CHART_KEY_INVESTMENT_NET:
            raise ValueError("chart_key investment_net is not valid for EXPENSE_LIMIT")
        return
    if goal_type == "INVESTMENT":
        # None = not tied to the investment chart (e.g. second+ savings goal). Only one
        # goal per user should use investment_net so the dashboard chart has a single link.
        if chart_key is not None and chart_key != CHART_KEY_INVESTMENT_NET:
            raise ValueError(
                "INVESTMENT goals only support chart_key 'investment_net' or omit it (unlinked)"
            )
        return
    if chart_key is not None:
        raise ValueError(f"chart_key is only supported for EXPENSE_LIMIT and INVESTMENT, not {goal_type}")


# Maps legacy linked_category (counterparty_category) to category:<series> when unambiguous.
_LINKED_CATEGORY_TO_SERIES: Final[dict[str, str]] = {
    "Food & Dining": "food_and_dining",
    "Shopping & E-commerce": "shopping",
    "Transport & Fuel": "transport",
    "Travel & Stay": "travel",
    "Gifts & Personal Transfers": "gifts",
}


def suggested_chart_key_for_linked_category(linked_category: str | None) -> str | None:
    """Best-effort chart_key for an EXPENSE_LIMIT that only had linked_category."""
    if not linked_category:
        return None
    s = _LINKED_CATEGORY_TO_SERIES.get(linked_category.strip())
    if s is None:
        return None
    return f"{CATEGORY_CHART_PREFIX}{s}"


def expense_limit_sum_for_chart_key(
    session: Session,
    chart_key: str,
    date_from,
    date_to,
) -> float:
    """Sum OUTFLOW amounts for EXPENSE_LIMIT goals tied to chart_key (metrics filters)."""
    # Late import: metrics imports this module; avoid circular import at load time.
    from api.services.query_helpers import _analytics_only, _date_where, _expense_where

    if chart_key == CHART_KEY_EXPENSE_NEED_WANT_STACK:
        base = _expense_where(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                col(Transaction.spend_category).in_(["NEED", "WANT"])
            )
        )
        q = _date_where(_analytics_only(base), date_from, date_to)
        return float(session.exec(q).one() or 0)

    series = parse_category_chart_key(chart_key)
    if series is not None:
        cond = category_trend_condition(series)
        base = _expense_where(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(cond)
        )
        q = _date_where(_analytics_only(base), date_from, date_to)
        return float(session.exec(q).one() or 0)

    raise ValueError(f"Not an expense chart_key: {chart_key!r}")
