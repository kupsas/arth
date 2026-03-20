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
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case
from sqlmodel import Session, col, func, select

from api.database import get_session
from api.models import Transaction

router = APIRouter()


# ───────────────────────────────────────────────────────────────────────────
# Business-logic constants
# ───────────────────────────────────────────────────────────────────────────

# txn_types excluded from income totals (these inflows aren't real income)
_INCOME_EXCLUSIONS: tuple[str, ...] = ("SELF_TRANSFER",)

# txn_types excluded from expense totals (these outflows aren't real spending)
_EXPENSE_EXCLUSIONS: tuple[str, ...] = ("CARD_PAYMENT", "SELF_TRANSFER")

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


# ───────────────────────────────────────────────────────────────────────────
# Internal helpers
# ───────────────────────────────────────────────────────────────────────────

def _current_month_range() -> tuple[datetime.date, datetime.date]:
    """Return (first day of current month, today)."""
    today = datetime.date.today()
    return today.replace(day=1), today


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


def _generate_month_labels(n: int) -> list[str]:
    """
    Generate a list of 'YYYY-MM' strings for the last n months (oldest first).

    Example: if today is March 2026 and n=3 → ['2026-01', '2026-02', '2026-03']

    We compute entirely with integer arithmetic to avoid dateutil dependency.
    'total_months' is the 0-based count of months since year 0:
        total_months = year * 12 + (month - 1)
    Going back i months: subtract i, then recover year and month via divmod.
    """
    today = datetime.date.today()
    base = today.year * 12 + (today.month - 1)  # 0-based month count
    labels = []
    for i in range(n - 1, -1, -1):
        total = base - i
        year, mo = divmod(total, 12)
        labels.append(f"{year:04d}-{mo + 1:02d}")
    return labels


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
    income_q = _date_where(
        _income_where(
            select(
                func.coalesce(func.sum(Transaction.amount), 0.0),
                func.count(Transaction.id),
            )
        ),
        date_from, date_to,
    )
    income_sum, income_count = session.exec(income_q).one()

    # ── Expense ─────────────────────────────────────────────────────────
    expense_q = _date_where(
        _expense_where(
            select(
                func.coalesce(func.sum(Transaction.amount), 0.0),
                func.count(Transaction.id),
            )
        ),
        date_from, date_to,
    )
    expense_sum, expense_count = session.exec(expense_q).one()

    # ── Savings (OUTFLOW to Asset Markets) ───────────────────────────────
    savings_q = _date_where(
        _savings_where(
            select(func.coalesce(func.sum(Transaction.amount), 0.0))
        ),
        date_from, date_to,
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
    base = select(
        Transaction.counterparty_category,
        func.sum(Transaction.amount).label("total"),
        func.count(Transaction.id).label("count"),
    )
    base = _date_where(base, date_from, date_to)

    # Apply direction + exclusion filters
    if direction == "OUTFLOW":
        base = _expense_where(base)
    else:
        base = _income_where(base)

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

    q = _date_where(
        _expense_where(
            select(
                Transaction.counterparty,
                func.max(Transaction.counterparty_category).label("category"),
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("count"),
            )
        ),
        date_from, date_to,
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
    income_q = _income_where(
        select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
    ).where(Transaction.txn_date >= cutoff).group_by(month_col)

    income_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(income_q).all()
    }

    # ── Expense by month ─────────────────────────────────────────────────
    expense_q = _expense_where(
        select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
    ).where(Transaction.txn_date >= cutoff).group_by(month_col)

    expense_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(expense_q).all()
    }

    # ── Savings by month (OUTFLOW to Asset Markets) ───────────────────────
    savings_q = _savings_where(
        select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
    ).where(Transaction.txn_date >= cutoff).group_by(month_col)

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
            _income_where(
                select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
            ).where(Transaction.txn_date >= cutoff).group_by(month_col)
        ).all()
    }

    expense_by_month: dict[str, float] = {
        row.month: float(row.total or 0)
        for row in session.exec(
            _expense_where(
                select(month_col.label("month"), func.sum(Transaction.amount).label("total"))
            ).where(Transaction.txn_date >= cutoff).group_by(month_col)
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


# ───────────────────────────────────────────────────────────────────────────
# GET /accounts-summary
# ───────────────────────────────────────────────────────────────────────────

@router.get("/accounts-summary", response_model=list[AccountRow])
def get_accounts_summary(
    *,
    session: Session = Depends(get_session),
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
        select(
            Transaction.account_id,
            func.count(Transaction.id).label("txn_count"),
            func.max(Transaction.txn_date).label("last_txn_date"),
            inflow_col,
            outflow_col,
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
) -> list[SpendCategoryRow]:
    """Return OUTFLOW spending broken down by NEED / WANT / SAVING / INVESTMENT.

    Excludes CARD_PAYMENT and SELF_TRANSFER from the "UNCLASSIFIED" bucket
    because they aren't real spending.  Does not include INFLOW transactions.

    This powers the "Spending Breakdown" donut chart on the dashboard.
    """
    q = (
        select(
            Transaction.spend_category,
            func.sum(Transaction.amount).label("amount"),
            func.count(Transaction.id).label("txn_count"),
        )
        .where(Transaction.direction == "OUTFLOW")
        .where(Transaction.txn_type.not_in(["CARD_PAYMENT", "SELF_TRANSFER"]))  # type: ignore[union-attr]
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
