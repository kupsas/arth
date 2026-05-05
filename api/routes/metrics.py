"""
Metrics API endpoints for the Arth dashboard.

Why these exclusions?
  CARD_PAYMENT  — paying the CC bill from savings. Individual purchases are
                  already captured as CARD_EXPENSE on the credit card statement,
                  so counting the CC bill payment too would double-count spending.
  SELF_TRANSFER — moving money between own accounts (savings ↔ brokerage, etc.).
                  Not income, not spending — just shuffling.

GET /api/metrics/summary             — income, expense, net, savings rate for a date range
GET /api/metrics/by-category         — spending (or income) ranked by counterparty_category
GET /api/metrics/top-counterparties  — top N merchants/payees by total spend
GET /api/metrics/monthly-trend       — month-by-month income vs expense for last N months
GET /api/metrics/accounts-summary    — per-account inflow/outflow totals (all time)
GET /api/metrics/negative-surplus-months — months where spending exceeded income (Q11)
GET /api/metrics/by-spend-category   — spending broken down by NEED/WANT/SAVING/INVESTMENT
GET /api/metrics/classification-stats — rules vs LLM vs user vs unclassified (Track 2 Phase 5d)
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, or_
from sqlmodel import Session, col, func, select

from api.auth import get_current_user
from api.errors import arth_validation_error
from api.database import get_session
from api.models import Goal, Transaction
from api.routes.transactions import _txn_to_dict
from api.services.chart_metrics import category_trend_condition
from api.services.goal_evaluator import expense_limit_spent_for_goal
from api.services.query_helpers import (
    _analytics_only,
    _current_month_range,
    _date_where,
    _expense_where,
    _for_user,
    _generate_month_labels,
    _income_where,
    _month_start_end,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Business-logic constants
# ───────────────────────────────────────────────────────────────────────────

# counterparty_category values that count as "savings" (money invested).
# Asset Markets = equities, MFs via ICICI Direct. Add others if needed
# (e.g. "Financial Services, Insurance & Banking" for PPF, insurance with savings component).
_SAVINGS_CATEGORIES: tuple[str, ...] = ("Asset Markets",)


# ───────────────────────────────────────────────────────────────────────────
# Response schemas (what each endpoint returns as JSON)
# ───────────────────────────────────────────────────────────────────────────

class MetricsSummary(BaseModel):
    date_from: str
    date_to: str
    total_income: float
    total_expense: float
    total_savings: float   # sum of OUTFLOW to Asset Markets (investments)
    net: float
    savings_rate: float   # total_savings / income * 100 (e.g. 42.5 = 42.5% invested)
    txn_count: int        # income + expense transaction count combined


class CategoryRow(BaseModel):
    category: str | None  # None = unclassified transactions
    amount: float
    percentage: float     # this category's share of the total for that direction
    txn_count: int


class CounterpartyRow(BaseModel):
    counterparty: str | None
    category: str | None
    amount: float
    txn_count: int


class MonthlyTrendRow(BaseModel):
    month: str            # "YYYY-MM" e.g. "2026-03"
    income: float
    expense: float
    net: float
    savings_rate: float


class AccountRow(BaseModel):
    account_id: str
    txn_count: int
    last_txn_date: str | None
    total_inflow: float
    total_outflow: float


class DeficitMonthRow(BaseModel):
    month: str       # "YYYY-MM"
    income: float
    expense: float
    net: float       # always negative for rows in this list


class NegativeSurplusResponse(BaseModel):
    months_with_deficit: int
    total_months: int
    deficit_months: list[DeficitMonthRow]
    total_deficit: float  # sum of |net| across deficit months (positive number)


class ClassificationStatsResponse(BaseModel):
    """Coarse distribution of ``transactions.classification_source`` for the session user."""

    total_transactions: int
    rules_pct: float
    llm_pct: float
    user_confirmed_pct: float
    unclassified_pct: float
    other_pct: float


# ───────────────────────────────────────────────────────────────────────────
# Internal helpers (metrics-specific; shared filters live in query_helpers)
# ───────────────────────────────────────────────────────────────────────────

def _savings_rate(income: float, total_savings: float) -> float:
    """
    Percentage of income that went into savings (investments).
    savings = OUTFLOW to Asset Markets. Returns 0.0 when income = 0.
    """
    if income <= 0:
        return 0.0
    return round(total_savings / income * 100, 2)


def _savings_where(q):
    """
    Narrow a query to savings transactions: OUTFLOW with counterparty_category
    in _SAVINGS_CATEGORIES (e.g. Asset Markets = equities, MFs).
    CARD_PAYMENT and SELF_TRANSFER are categorised as Self Transfer, not Asset Markets,
    so the category filter is sufficient.
    """
    return q.where(Transaction.direction == "OUTFLOW").where(
        col(Transaction.counterparty_category).in_(_SAVINGS_CATEGORIES)
    )


# Investment flow types (purchases = OUTFLOW, sales = INFLOW proceeds)
_PURCHASE_TXN_TYPES: tuple[str, ...] = ("EQUITY_PURCHASE", "MF_PURCHASE")
_SALE_TXN_TYPES: tuple[str, ...] = ("EQUITY_SALE", "MF_SALE")


# ───────────────────────────────────────────────────────────────────────────
# GET /summary
# ───────────────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=MetricsSummary)
def get_summary(
    date_from: datetime.date | None = Query(
        None, description="Start date (inclusive). Defaults to first day of current month."
    ),
    date_to: datetime.date | None = Query(
        None, description="End date (inclusive). Defaults to today."
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """
    High-level summary metrics for the given date range.

    Defaults to the current calendar month so the dashboard loads with
    something meaningful without the caller needing to pass dates.
    """
    df, dt = _current_month_range()
    date_from = date_from or df
    date_to = date_to or dt

    # ── Income ──────────────────────────────────────────────────────────
    income_q = _for_user(
        _date_where(
            _analytics_only(
                _income_where(
                    select(
                        func.coalesce(func.sum(Transaction.amount), 0.0),
                        func.count(Transaction.id),
                    )
                )
            ),
            date_from, date_to,
        ),
        current_user,
    )
    income_sum, income_count = session.exec(income_q).one()

    # ── Expense ─────────────────────────────────────────────────────────
    expense_q = _for_user(
        _date_where(
            _analytics_only(
                _expense_where(
                    select(
                        func.coalesce(func.sum(Transaction.amount), 0.0),
                        func.count(Transaction.id),
                    )
                )
            ),
            date_from, date_to,
        ),
        current_user,
    )
    expense_sum, expense_count = session.exec(expense_q).one()

    # ── Savings (OUTFLOW to Asset Markets) ───────────────────────────────
    savings_q = _for_user(
        _date_where(
            _analytics_only(
                _savings_where(
                    select(func.coalesce(func.sum(Transaction.amount), 0.0))
                )
            ),
            date_from, date_to,
        ),
        current_user,
    )
    total_savings = round(float(session.exec(savings_q).one() or 0), 2)

    total_income = round(float(income_sum), 2)
    total_expense = round(float(expense_sum), 2)

    return MetricsSummary(
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        total_income=total_income,
        total_expense=total_expense,
        total_savings=total_savings,
        net=round(total_income - total_expense, 2),
        savings_rate=_savings_rate(total_income, total_savings),
        txn_count=income_count + expense_count,
    )


# ───────────────────────────────────────────────────────────────────────────
# GET /by-category
# ───────────────────────────────────────────────────────────────────────────

@router.get("/by-category", response_model=list[CategoryRow])
def get_by_category(
    date_from: datetime.date | None = Query(None),
    date_to: datetime.date | None = Query(None),
    direction: str = Query(
        "OUTFLOW",
        description="Which side to break down: INFLOW or OUTFLOW (default OUTFLOW = expenses).",
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """
    Spending (or income) ranked by counterparty_category.

    Answers: "Where does my money actually go each month?"
    """
    df, dt = _current_month_range()
    date_from = date_from or df
    date_to = date_to or dt
    direction = direction.upper()

    # Build the base aggregation query
    base = _for_user(
        select(
            Transaction.counterparty_category,
            func.sum(Transaction.amount).label("total"),
            func.count(Transaction.id).label("count"),
        ),
        current_user,
    )
    base = _date_where(base, date_from, date_to)

    # Apply direction + exclusion filters
    if direction == "OUTFLOW":
        base = _expense_where(base)
    else:
        base = _income_where(base)

    base = _analytics_only(base)

    base = (
        base.group_by(Transaction.counterparty_category)
        .order_by(func.sum(Transaction.amount).desc())
    )

    rows = session.exec(base).all()
    grand_total = sum(float(r.total or 0) for r in rows) or 1.0  # avoid div/0

    return [
        CategoryRow(
            category=r.counterparty_category,
            amount=round(float(r.total or 0), 2),
            percentage=round(float(r.total or 0) / grand_total * 100, 2),
            txn_count=r.count,
        )
        for r in rows
    ]


# ───────────────────────────────────────────────────────────────────────────
# GET /top-counterparties
# ───────────────────────────────────────────────────────────────────────────

@router.get("/top-counterparties", response_model=list[CounterpartyRow])
def get_top_counterparties(
    date_from: datetime.date | None = Query(None),
    date_to: datetime.date | None = Query(None),
    limit: int = Query(10, ge=1, le=50, description="Max number of counterparties to return."),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """
    Top merchants/payees ranked by total spend (OUTFLOW only).

    Uses func.max(category) as a proxy for "the category of this counterparty"
    — in practice a counterparty has one consistent category (e.g. Swiggy is
    always 'Swiggy'), so max() gives the right answer without a subquery.
    """
    df, dt = _current_month_range()
    date_from = date_from or df
    date_to = date_to or dt

    q = _for_user(
        _date_where(
            _analytics_only(
                _expense_where(
                    select(
                        Transaction.counterparty,
                        func.max(Transaction.counterparty_category).label("category"),
                        func.sum(Transaction.amount).label("total"),
                        func.count(Transaction.id).label("count"),
                    )
                )
            ),
            date_from, date_to,
        ),
        current_user,
    )

    q = (
        q.group_by(Transaction.counterparty)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(limit)
    )

    rows = session.exec(q).all()

    return [
        CounterpartyRow(
            counterparty=r.counterparty,
            category=r.category,
            amount=round(float(r.total or 0), 2),
            txn_count=r.count,
        )
        for r in rows
    ]


# ───────────────────────────────────────────────────────────────────────────
# GET /monthly-trend
# ───────────────────────────────────────────────────────────────────────────

@router.get("/monthly-trend", response_model=list[MonthlyTrendRow])
def get_monthly_trend(
    months: int = Query(
        12, ge=1, le=36, description="How many trailing months to include (includes current month)."
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """
    Month-by-month income vs expense for the last N months.

    Returns one row per month even if no transactions exist for that month
    (those will have income=0, expense=0, etc.). This lets the frontend
    render a smooth chart without gaps.

    Two separate queries (income by month, expense by month) are merged in
    Python to keep the SQL simple and avoid complex CASE-based pivoting.
    """
    # Compute the cutoff date (first day of the earliest month we care about)
    today = datetime.date.today()
    base_total = today.year * 12 + (today.month - 1)
    start_total = base_total - (months - 1)
    start_year, start_mo = divmod(start_total, 12)
    cutoff = datetime.date(start_year, start_mo + 1, 1)

    # SQLite's strftime('%Y-%m', date_column) returns e.g. '2026-03'
    month_col = func.strftime("%Y-%m", Transaction.txn_date)

    # ── Income by month ──────────────────────────────────────────────────
    income_q = _for_user(
        _analytics_only(
            _income_where(
                select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
            )
        ).where(Transaction.txn_date >= cutoff).group_by(month_col),
        current_user,
    )

    income_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(income_q).all()
    }

    # ── Expense by month ─────────────────────────────────────────────────
    expense_q = _for_user(
        _analytics_only(
            _expense_where(
                select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
            )
        ).where(Transaction.txn_date >= cutoff).group_by(month_col),
        current_user,
    )

    expense_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(expense_q).all()
    }

    # ── Savings by month (OUTFLOW to Asset Markets) ───────────────────────
    savings_q = _for_user(
        _analytics_only(
            _savings_where(
                select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
            )
        ).where(Transaction.txn_date >= cutoff).group_by(month_col),
        current_user,
    )

    savings_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(savings_q).all()
    }

    # ── Merge into ordered list, zero-filling missing months ─────────────
    result = []
    for label in _generate_month_labels(months):
        income = round(income_by_month.get(label, 0.0), 2)
        expense = round(expense_by_month.get(label, 0.0), 2)
        savings = round(savings_by_month.get(label, 0.0), 2)
        result.append(
            MonthlyTrendRow(
                month=label,
                income=income,
                expense=expense,
                net=round(income - expense, 2),
                savings_rate=_savings_rate(income, savings),
            )
        )

    return result


# ───────────────────────────────────────────────────────────────────────────
# GET /negative-surplus-months  (Q11)
# ───────────────────────────────────────────────────────────────────────────

@router.get("/negative-surplus-months", response_model=NegativeSurplusResponse)
def get_negative_surplus_months(
    months: int = Query(
        12, ge=1, le=36,
        description="How many trailing months to scan (default 12, max 36)."
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """
    Months where spending exceeded income (net < 0).

    Answers Q11: "How many of my recent months had a budget deficit?"

    Reuses the same monthly trend query — computes income and expense by month,
    then filters for months where net < 0.  Returns the count, the list of
    specific months, and the total deficit amount.
    """
    today = datetime.date.today()
    base_total = today.year * 12 + (today.month - 1)
    start_total = base_total - (months - 1)
    start_year, start_mo = divmod(start_total, 12)
    cutoff = datetime.date(start_year, start_mo + 1, 1)

    month_col = func.strftime("%Y-%m", Transaction.txn_date)

    income_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(
            _for_user(
                _analytics_only(
                    _income_where(
                        select(
                            month_col.label("month"),
                            func.sum(Transaction.amount).label("total"),
                        )
                    )
                ).where(Transaction.txn_date >= cutoff).group_by(month_col),
                current_user,
            )
        ).all()
    }

    expense_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(
            _for_user(
                _analytics_only(
                    _expense_where(
                        select(
                            month_col.label("month"),
                            func.sum(Transaction.amount).label("total"),
                        )
                    )
                ).where(Transaction.txn_date >= cutoff).group_by(month_col),
                current_user,
            )
        ).all()
    }

    deficit_months: list[DeficitMonthRow] = []
    for label in _generate_month_labels(months):
        income = round(income_by_month.get(label, 0.0), 2)
        expense = round(expense_by_month.get(label, 0.0), 2)
        net = round(income - expense, 2)
        if net < 0:
            deficit_months.append(
                DeficitMonthRow(month=label, income=income, expense=expense, net=net)
            )

    return NegativeSurplusResponse(
        months_with_deficit=len(deficit_months),
        total_months=months,
        deficit_months=deficit_months,
        total_deficit=round(sum(abs(m.net) for m in deficit_months), 2),
    )


def _bucket_classification_source(src: str | None) -> str:
    """Align with ``scripts/compare_onboarding.py`` — coarse buckets for UI."""
    if src is None or not str(src).strip():
        return "unclassified"
    u = str(src).strip().upper()
    if u.startswith("RULES"):
        return "rules"
    if u.startswith("LLM"):
        return "llm"
    if u.startswith("USER"):
        return "user"
    return "other"


@router.get("/classification-stats", response_model=ClassificationStatsResponse)
def get_classification_stats(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """How transactions were classified: rules engine, LLM, user edits, or still blank."""
    q = (
        select(Transaction.classification_source, func.count(Transaction.id))
        .where(Transaction.user_id == current_user)
        .group_by(Transaction.classification_source)
    )
    rows = session.exec(q).all()
    buckets: dict[str, int] = {
        "rules": 0,
        "llm": 0,
        "user": 0,
        "unclassified": 0,
        "other": 0,
    }
    total = 0
    for src, cnt in rows:
        b = _bucket_classification_source(src)
        buckets[b] = buckets.get(b, 0) + int(cnt)
        total += int(cnt)
    if total == 0:
        return ClassificationStatsResponse(
            total_transactions=0,
            rules_pct=0.0,
            llm_pct=0.0,
            user_confirmed_pct=0.0,
            unclassified_pct=0.0,
            other_pct=0.0,
        )

    def pct(n: int) -> float:
        return round(100.0 * n / total, 1)

    return ClassificationStatsResponse(
        total_transactions=total,
        rules_pct=pct(buckets["rules"]),
        llm_pct=pct(buckets["llm"]),
        user_confirmed_pct=pct(buckets["user"]),
        unclassified_pct=pct(buckets["unclassified"]),
        other_pct=pct(buckets["other"]),
    )


# ───────────────────────────────────────────────────────────────────────────
# GET /accounts-summary
# ───────────────────────────────────────────────────────────────────────────

@router.get("/accounts-summary", response_model=list[AccountRow])
def get_accounts_summary(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """
    Per-account totals across all time.

    Uses a SQL CASE expression to compute inflow and outflow sums in a
    single pass rather than two separate queries. CASE works like an
    if/else inside an aggregate:
        SUM(CASE WHEN direction='INFLOW' THEN amount ELSE 0 END)
    """
    inflow_col = func.sum(
        case((Transaction.direction == "INFLOW", Transaction.amount), else_=0.0)
    ).label("total_inflow")

    outflow_col = func.sum(
        case((Transaction.direction == "OUTFLOW", Transaction.amount), else_=0.0)
    ).label("total_outflow")

    q = (
        _for_user(
            _analytics_only(
                select(
                    Transaction.account_id,
                    func.count(Transaction.id).label("txn_count"),
                    func.max(Transaction.txn_date).label("last_txn_date"),
                    inflow_col,
                    outflow_col,
                )
            ),
            current_user,
        )
        .group_by(Transaction.account_id)
        .order_by(Transaction.account_id)
    )

    rows = session.exec(q).all()

    return [
        AccountRow(
            account_id=r.account_id,
            txn_count=r.txn_count,
            last_txn_date=(
                r.last_txn_date.isoformat()
                if isinstance(r.last_txn_date, datetime.date)
                else r.last_txn_date  # already a string in SQLite
            ),
            total_inflow=round(float(r.total_inflow or 0), 2),
            total_outflow=round(float(r.total_outflow or 0), 2),
        )
        for r in rows
    ]


# ───────────────────────────────────────────────────────────────────────────
# GET /by-spend-category  — NEED / WANT / SAVING / INVESTMENT breakdown
# ───────────────────────────────────────────────────────────────────────────

class SpendCategoryRow(BaseModel):
    spend_category: str     # "NEED" | "WANT" | "SAVING" | "INVESTMENT" | "UNCLASSIFIED"
    amount: float
    percentage: float       # 0–100 (share of total classified outflow)
    txn_count: int


@router.get("/by-spend-category", response_model=list[SpendCategoryRow])
def metrics_by_spend_category(
    date_from: datetime.date | None = Query(None),
    date_to: datetime.date | None = Query(None),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> list[SpendCategoryRow]:
    """Return OUTFLOW spending broken down by NEED / WANT / SAVING / INVESTMENT.

    Excludes CARD_PAYMENT and SELF_TRANSFER from the "UNCLASSIFIED" bucket
    because they aren't real spending.  Does not include INFLOW transactions.

    This powers the "Spending Breakdown" donut chart on the dashboard.
    """
    q = _for_user(
        _analytics_only(
            select(
                Transaction.spend_category,
                func.sum(Transaction.amount).label("amount"),
                func.count(Transaction.id).label("txn_count"),
            )
            .where(Transaction.direction == "OUTFLOW")
            .where(Transaction.txn_type.not_in(["CARD_PAYMENT", "SELF_TRANSFER"]))  # type: ignore[union-attr]
        ),
        current_user,
    )

    if date_from:
        q = q.where(Transaction.txn_date >= date_from)
    if date_to:
        q = q.where(Transaction.txn_date <= date_to)

    q = q.group_by(Transaction.spend_category).order_by(
        func.sum(Transaction.amount).desc()
    )

    rows = session.exec(q).all()
    total = sum(float(r.amount or 0) for r in rows)

    return [
        SpendCategoryRow(
            spend_category=r.spend_category or "UNCLASSIFIED",
            amount=round(float(r.amount or 0), 2),
            percentage=round(float(r.amount or 0) / total * 100, 1) if total > 0 else 0.0,
            txn_count=r.txn_count,
        )
        for r in rows
    ]


# ───────────────────────────────────────────────────────────────────────────
# Dashboard V2 — goal progress, trends, drill-down
# ───────────────────────────────────────────────────────────────────────────


def _adherence_month_labels(n: int = 4) -> list[str]:
    """Oldest-first list of YYYY-MM for the last n calendar months (incl. current)."""
    today = datetime.date.today()
    y, m = today.year, today.month
    out: list[str] = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def _total_expenses_month(
    session: Session, start: datetime.date, end: datetime.date, user_id: str
) -> float:
    q = _for_user(
        _date_where(
            _analytics_only(
                _expense_where(select(func.coalesce(func.sum(Transaction.amount), 0.0)))
            ),
            start,
            end,
        ),
        user_id,
    )
    return float(session.exec(q).one() or 0)


def _investment_flows_month(
    session: Session, start: datetime.date, end: datetime.date, user_id: str
) -> tuple[float, float, float]:
    """Returns (purchases, sales, net) for the month window."""
    pur_q = _for_user(
        _analytics_only(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                Transaction.direction == "OUTFLOW",
                col(Transaction.txn_type).in_(_PURCHASE_TXN_TYPES),
            )
        ),
        user_id,
    )
    pur_q = _date_where(pur_q, start, end)
    purchases = float(session.exec(pur_q).one() or 0)

    sale_q = _for_user(
        _analytics_only(
            select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                Transaction.direction == "INFLOW",
                col(Transaction.txn_type).in_(_SALE_TXN_TYPES),
            )
        ),
        user_id,
    )
    sale_q = _date_where(sale_q, start, end)
    sales = float(session.exec(sale_q).one() or 0)

    return purchases, sales, round(purchases - sales, 2)


class AdherenceMonth(BaseModel):
    month: str
    hit: bool | None  # None when target_amount is unset
    # Net investment (INVESTMENT) or spend in scope (EXPENSE_LIMIT) for that month — for UI tooltips.
    amount: float | None = None


class GoalProgressResponse(BaseModel):
    goal_id: int
    goal_type: str
    target_amount: float | None
    current_value: float
    purchases: float | None = None
    sales: float | None = None
    net_investment: float | None = None
    adherence: list[AdherenceMonth]
    progress_cadence: str = "MONTHLY"


@router.get("/goal-progress", response_model=GoalProgressResponse)
def get_goal_progress(
    goal_id: int = Query(..., description="Goal id to evaluate"),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    goal = session.get(Goal, goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    if goal.user_id != current_user:
        raise HTTPException(status_code=403, detail="Goal belongs to another user")

    today = datetime.date.today()
    cur_start = today.replace(day=1)
    adherence: list[AdherenceMonth] = []
    target = goal.target_amount

    if goal.goal_type == "INVESTMENT":
        pur, sal, net = _investment_flows_month(session, cur_start, today, current_user)
        current = net
        for ym in _adherence_month_labels(4):
            ms, me = _month_start_end(ym)
            _, _, mnet = _investment_flows_month(session, ms, me, current_user)
            if target is None or target <= 0:
                adherence.append(AdherenceMonth(month=ym, hit=None, amount=mnet))
            else:
                adherence.append(
                    AdherenceMonth(month=ym, hit=mnet >= target, amount=mnet)
                )
        return GoalProgressResponse(
            goal_id=goal.id,
            goal_type=goal.goal_type,
            target_amount=target,
            current_value=current,
            purchases=round(pur, 2),
            sales=round(sal, 2),
            net_investment=net,
            adherence=adherence,
            progress_cadence="MONTHLY",
        )

    if goal.goal_type == "EXPENSE_LIMIT":
        cadence = (getattr(goal, "progress_cadence", None) or "MONTHLY").upper()
        if cadence == "ANNUAL":
            year_start = today.replace(month=1, day=1)
            current = expense_limit_spent_for_goal(goal, session, year_start, today)
            adherence = []
        else:
            current = expense_limit_spent_for_goal(goal, session, cur_start, today)
            for ym in _adherence_month_labels(4):
                ms, me = _month_start_end(ym)
                spent = expense_limit_spent_for_goal(goal, session, ms, me)
                if target is None or target <= 0:
                    adherence.append(AdherenceMonth(month=ym, hit=None, amount=spent))
                else:
                    adherence.append(
                        AdherenceMonth(month=ym, hit=spent <= target, amount=spent)
                    )
        return GoalProgressResponse(
            goal_id=goal.id,
            goal_type=goal.goal_type,
            target_amount=target,
            current_value=round(current, 2),
            adherence=adherence,
            progress_cadence=cadence,
        )

    raise HTTPException(
        status_code=400,
        detail="goal-progress supports goal_type INVESTMENT or EXPENSE_LIMIT only",
    )


class InvestmentTrendRow(BaseModel):
    month: str
    purchases: float
    sales: float
    net: float


@router.get("/investment-trend", response_model=list[InvestmentTrendRow])
def get_investment_trend(
    months: int = Query(6, ge=1, le=36),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    out: list[InvestmentTrendRow] = []
    for ym in _generate_month_labels(months):
        ms, me = _month_start_end(ym)
        pur, sal, net = _investment_flows_month(session, ms, me, current_user)
        out.append(
            InvestmentTrendRow(
                month=ym,
                purchases=round(pur, 2),
                sales=round(sal, 2),
                net=net,
            )
        )
    return out


class ExpenseStackedRow(BaseModel):
    month: str
    need: float
    want: float


@router.get("/expense-trend-stacked", response_model=list[ExpenseStackedRow])
def get_expense_trend_stacked(
    months: int = Query(6, ge=1, le=36),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    today = datetime.date.today()
    base_total = today.year * 12 + (today.month - 1)
    start_total = base_total - (months - 1)
    start_year, start_mo = divmod(start_total, 12)
    cutoff = datetime.date(start_year, start_mo + 1, 1)
    month_col = func.strftime("%Y-%m", Transaction.txn_date)

    q = (
        _for_user(
            _analytics_only(
                _expense_where(
                    select(
                        month_col.label("month"),
                        Transaction.spend_category,
                        func.sum(Transaction.amount).label("total"),
                    ).where(col(Transaction.spend_category).in_(["NEED", "WANT"]))
                )
            )
            .where(Transaction.txn_date >= cutoff)
            .group_by(month_col, Transaction.spend_category),
            current_user,
        )
    )

    buckets: dict[str, dict[str, float]] = {}
    for row in session.exec(q).all():
        buckets.setdefault(row.month, {})[row.spend_category or ""] = float(row.total or 0)

    return [
        ExpenseStackedRow(
            month=label,
            need=round(buckets.get(label, {}).get("NEED", 0.0), 2),
            want=round(buckets.get(label, {}).get("WANT", 0.0), 2),
        )
        for label in _generate_month_labels(months)
    ]


class CategoryTrendRow(BaseModel):
    month: str
    amount: float


@router.get("/category-trend", response_model=list[CategoryTrendRow])
def get_category_trend(
    series: str = Query(
        ...,
        description=(
            "swiggy_instamart | swiggy_food | food_and_dining | gifts | "
            "shopping | transport | travel"
        ),
    ),
    months: int = Query(6, ge=1, le=36),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    try:
        cond = category_trend_condition(series)
    except ValueError as e:
        logger.warning("Metrics category-trend: unknown series=%s", series)
        raise arth_validation_error(str(e)) from e

    today = datetime.date.today()
    base_total = today.year * 12 + (today.month - 1)
    start_total = base_total - (months - 1)
    start_year, start_mo = divmod(start_total, 12)
    cutoff = datetime.date(start_year, start_mo + 1, 1)
    month_col = func.strftime("%Y-%m", Transaction.txn_date)

    q = (
        _for_user(
            _analytics_only(
                _expense_where(
                    select(
                        month_col.label("month"),
                        func.sum(Transaction.amount).label("total"),
                    ).where(cond)
                )
            )
            .where(Transaction.txn_date >= cutoff)
            .group_by(month_col),
            current_user,
        )
    )
    by_m = {row.month: float(row.total or 0) for row in session.exec(q).all()}
    return [
        CategoryTrendRow(month=label, amount=round(by_m.get(label, 0.0), 2))
        for label in _generate_month_labels(months)
    ]


@router.get("/top-expenses", response_model=list[dict])
def get_top_expenses(
    threshold: float = Query(5000, ge=0),
    year_month: str | None = Query(
        None,
        description="YYYY-MM (default: current calendar month)",
        pattern=r"^\d{4}-\d{2}$",
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    today = datetime.date.today()
    if year_month:
        start, end = _month_start_end(year_month)
    else:
        start = today.replace(day=1)
        end = today

    q = (
        _for_user(
            _analytics_only(
                _expense_where(select(Transaction).where(Transaction.amount >= threshold))
            )
            .where(Transaction.txn_date >= start)
            .where(Transaction.txn_date <= end)
            .order_by(col(Transaction.amount).desc()),
            current_user,
        )
    )
    rows = session.exec(q).all()
    return [_txn_to_dict(t) for t in rows]


@router.get("/bar-drilldown", response_model=list[dict])
def get_bar_drilldown(
    chart: str = Query(
        ...,
        description=(
            "investment_purchase | investment_sale | investment_month | "
            "expense_need | expense_want | category"
        ),
    ),
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    series: str | None = Query(
        None,
        description="Required when chart=category — same values as /category-trend",
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    start, end = _month_start_end(month)
    q = _for_user(select(Transaction), current_user)

    if chart == "investment_purchase":
        q = q.where(Transaction.direction == "OUTFLOW").where(
            col(Transaction.txn_type).in_(_PURCHASE_TXN_TYPES)
        )
    elif chart == "investment_sale":
        q = q.where(Transaction.direction == "INFLOW").where(
            col(Transaction.txn_type).in_(_SALE_TXN_TYPES)
        )
    elif chart == "investment_month":
        q = q.where(
            or_(
                (Transaction.direction == "OUTFLOW")
                & col(Transaction.txn_type).in_(_PURCHASE_TXN_TYPES),
                (Transaction.direction == "INFLOW")
                & col(Transaction.txn_type).in_(_SALE_TXN_TYPES),
            )
        )
    elif chart == "expense_need":
        q = _expense_where(q).where(Transaction.spend_category == "NEED")
    elif chart == "expense_want":
        q = _expense_where(q).where(Transaction.spend_category == "WANT")
    elif chart == "category":
        if not series:
            raise HTTPException(status_code=400, detail="series is required when chart=category")
        try:
            cond = category_trend_condition(series)
        except ValueError as e:
            logger.warning("Metrics bar-drilldown: unknown category series=%s", series)
            raise arth_validation_error(str(e)) from e
        q = _expense_where(q).where(cond)
    else:
        raise HTTPException(status_code=400, detail=f"unknown chart: {chart}")

    q = _analytics_only(q)
    q = q.where(Transaction.txn_date >= start).where(Transaction.txn_date <= end)
    q = q.order_by(col(Transaction.txn_date).desc(), col(Transaction.amount).desc())
    rows = session.exec(q).all()
    return [_txn_to_dict(t) for t in rows]
