"""
Monthly surplus for goals / simulation (Sub-Plan B).

Surplus = recurring income − recurring baseline expenses, smoothed with a
rolling median (default 6 months). Conservative estimate uses the minimum of:

- **Path A:** Category-filtered outflows (stable counterparty_category buckets).
- **Path B:** NEED spend for each month + median of WANT totals across the window.

Income baseline comes from active :class:`~api.models.RecurringPattern` INFLOW
rows (not raw transaction totals), so one-off credits do not inflate income.
"""

from __future__ import annotations

import datetime
from statistics import median

from pydantic import BaseModel, Field
from sqlmodel import Session, col, func, select

from api.models import RecurringPattern, Transaction
from api.services.query_helpers import (
    _analytics_only,
    _date_where,
    _expense_where,
    _for_user,
    _generate_month_labels,
)

# ───────────────────────────────────────────────────────────────────────────
# Category sets (counterparty_category) — see docs/personal-data/thoughts_on_goals.md
# ───────────────────────────────────────────────────────────────────────────

# Stable “recurring baseline” expenses (Path A includes only these buckets).
RECURRING_EXPENSE_CATEGORIES: frozenset[str] = frozenset(
    {
        "Rent & Housing",
        "Utilities & Internet",
        "Mobile, OTT & Subscriptions",
        "Transport & Fuel",
        "Food & Dining",
        "Swiggy",
        "Fees, Charges & Interest",
        "Financial Services, Insurance & Banking",
    }
)

# Frequency → multiply expected_amount to approximate a monthly INR amount.
_FREQ_TO_MONTHLY_MULT: dict[str, float] = {
    "WEEKLY": 4.33,
    "MONTHLY": 1.0,
    "QUARTERLY": 1.0 / 3.0,
    "YEARLY": 1.0 / 12.0,
}


class MonthDetail(BaseModel):
    """One calendar month within the analysis window."""

    month: str = Field(..., description="YYYY-MM")
    income: float = Field(..., description="Recurring income (monthly-equivalent snapshot)")
    expense_category_filtered: float = Field(..., description="Path A: included categories only")
    expense_need: float = Field(..., description="Path B: NEED outflows")
    expense_want: float = Field(..., description="Path B: WANT outflows (per month)")
    surplus_path_a: float = Field(..., description="income − Path A expense")
    surplus_path_b: float = Field(
        ...,
        description="income − NEED_this_month − median(WANT over window)",
    )


class SurplusResult(BaseModel):
    """Headline surplus + breakdown for API and simulation."""

    user_id: str
    monthly_income: float = Field(
        ...,
        description="Current recurring monthly income from active INFLOW patterns",
    )
    monthly_expense_baseline: float = Field(
        ...,
        description="Implied baseline spend: monthly_income − monthly_surplus",
    )
    monthly_surplus: float = Field(
        ...,
        description="Median of monthly min(Path A surplus, Path B surplus)",
    )
    surplus_path_a: float = Field(
        ...,
        description="Median of monthly Path A surplus values (category-filtered)",
    )
    surplus_path_b: float = Field(
        ...,
        description="Median of monthly Path B surplus values (NEED + median WANT)",
    )
    computation_method: str = Field(default="conservative_min_of_dual_path_median")
    months_analyzed: int
    month_details: list[MonthDetail]
    recurring_income_patterns: list[dict]
    warnings: list[str]


def _monthly_equivalent_inflow(pattern: RecurringPattern) -> float:
    mult = _FREQ_TO_MONTHLY_MULT.get(pattern.frequency, 1.0)
    return float(pattern.expected_amount) * mult


def _recurring_monthly_income(session: Session, user_id: str) -> tuple[float, list[dict]]:
    """Sum active INFLOW patterns for this user; return (total, pattern detail dicts)."""
    patterns = session.exec(
        select(RecurringPattern).where(
            RecurringPattern.user_id == user_id,
            RecurringPattern.direction == "INFLOW",
            RecurringPattern.is_active == True,  # noqa: E712
        )
    ).all()
    details: list[dict] = []
    total = 0.0
    for p in patterns:
        m = _monthly_equivalent_inflow(p)
        total += m
        details.append(
            {
                "id": p.id,
                "counterparty": p.counterparty,
                "expected_amount": float(p.expected_amount),
                "frequency": p.frequency,
                "monthly_equivalent_inr": round(m, 2),
            }
        )
    return round(total, 2), details


def _allowed_account_ids(session: Session, user_id: str) -> list[str]:
    """Distinct bank ``account_id`` values that have transactions for this user."""
    rows = session.exec(
        select(Transaction.account_id)
        .where(Transaction.user_id == user_id)
        .distinct()
    ).all()
    return [str(aid) for aid in rows if aid]


def _expense_path_a_month(
    session: Session,
    date_from: datetime.date,
    date_to: datetime.date,
    allowed_accounts: list[str],
    user_id: str,
) -> float:
    """Path A: recurring-baseline expense from counterparty_category buckets."""
    if not allowed_accounts:
        return 0.0
    q = select(func.coalesce(func.sum(Transaction.amount), 0.0))
    q = _for_user(q, user_id)
    q = _expense_where(q)
    q = _analytics_only(q)
    q = _date_where(q, date_from, date_to)
    q = q.where(col(Transaction.counterparty_category).in_(RECURRING_EXPENSE_CATEGORIES))
    q = q.where(col(Transaction.account_id).in_(allowed_accounts))
    return float(session.exec(q).one() or 0)


def _expense_need_want_month(
    session: Session,
    date_from: datetime.date,
    date_to: datetime.date,
    allowed_accounts: list[str],
    user_id: str,
) -> tuple[float, float]:
    """Path B: NEED and WANT sums for the month."""
    if not allowed_accounts:
        return 0.0, 0.0

    need_q = select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
        Transaction.spend_category == "NEED"
    )
    need_q = _for_user(need_q, user_id)
    need_q = _expense_where(need_q)
    need_q = _analytics_only(need_q)
    need_q = _date_where(need_q, date_from, date_to)
    need_q = need_q.where(col(Transaction.account_id).in_(allowed_accounts))

    want_q = select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
        Transaction.spend_category == "WANT"
    )
    want_q = _for_user(want_q, user_id)
    want_q = _expense_where(want_q)
    want_q = _analytics_only(want_q)
    want_q = _date_where(want_q, date_from, date_to)
    want_q = want_q.where(col(Transaction.account_id).in_(allowed_accounts))

    need_sum = float(session.exec(need_q).one() or 0)
    want_sum = float(session.exec(want_q).one() or 0)
    return need_sum, want_sum


def _month_start_end(ym: str) -> tuple[datetime.date, datetime.date]:
    from api.services.query_helpers import _last_day_of_calendar_month

    y, m = map(int, ym.split("-"))
    start = datetime.date(y, m, 1)
    end = _last_day_of_calendar_month(y, m)
    today = datetime.date.today()
    if end > today:
        end = today
    return start, end


def compute_surplus(
    session: Session,
    user_id: str,
    months: int = 6,
) -> SurplusResult:
    """
    Compute smoothed monthly surplus for ``user_id``.

    :param months: Number of trailing calendar months (including current). Clamped 3–12.
    """
    warnings: list[str] = []
    if months < 3:
        warnings.append("months < 3 requested; using minimum of 3 for stable median")
        months = 3
    elif months > 12:
        months = 12

    allowed = _allowed_account_ids(session, user_id)
    if not allowed:
        warnings.append("No transactions on accounts mapped to this user; expense baseline is zero")

    monthly_income, recurring_income_patterns = _recurring_monthly_income(session, user_id)
    if monthly_income <= 0:
        warnings.append("No active recurring INFLOW patterns; income baseline is zero")

    labels = _generate_month_labels(months)
    month_details: list[MonthDetail] = []
    want_totals: list[float] = []

    for ym in labels:
        start, end = _month_start_end(ym)
        exp_a = _expense_path_a_month(session, start, end, allowed, user_id)
        need_b, want_b = _expense_need_want_month(session, start, end, allowed, user_id)
        want_totals.append(want_b)
        s_a = round(monthly_income - exp_a, 2)
        month_details.append(
            MonthDetail(
                month=ym,
                income=monthly_income,
                expense_category_filtered=round(exp_a, 2),
                expense_need=round(need_b, 2),
                expense_want=round(want_b, 2),
                surplus_path_a=s_a,
                surplus_path_b=0.0,
            )
        )

    median_want = median(want_totals) if want_totals else 0.0

    for i, md in enumerate(month_details):
        s_b = round(monthly_income - md.expense_need - median_want, 2)
        month_details[i] = md.model_copy(update={"surplus_path_b": s_b})

    surpluses_a = [m.surplus_path_a for m in month_details]
    surpluses_b = [m.surplus_path_b for m in month_details]
    conservative = [min(a, b) for a, b in zip(surpluses_a, surpluses_b, strict=True)]

    median_surplus = round(median(conservative), 2) if conservative else 0.0
    med_a = round(median(surpluses_a), 2) if surpluses_a else 0.0
    med_b = round(median(surpluses_b), 2) if surpluses_b else 0.0

    baseline_expense = round(monthly_income - median_surplus, 2)

    return SurplusResult(
        user_id=user_id,
        monthly_income=monthly_income,
        monthly_expense_baseline=baseline_expense,
        monthly_surplus=median_surplus,
        surplus_path_a=med_a,
        surplus_path_b=med_b,
        computation_method="conservative_min_of_dual_path_median",
        months_analyzed=len(labels),
        month_details=month_details,
        recurring_income_patterns=recurring_income_patterns,
        warnings=warnings,
    )
