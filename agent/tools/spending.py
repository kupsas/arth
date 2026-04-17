"""
Spending-domain tools — facades over ``/api/metrics/*``, ``/api/recurring/*``, ``/api/transactions``.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from agent.tools.base import tool


@tool(
    name="get_spending_summary",
    description=(
        "Get total income, expenses, savings (investments to Asset Markets), net, "
        "savings rate, and transaction count for a date range. "
        "Use when the user asks about overall spending, savings rate, or a monthly "
        "financial summary. Defaults to the current calendar month if dates are omitted. "
        "For category-level spending or optional large-transaction rows, use get_spending_by_category."
    ),
)
async def get_spending_summary(
    client: AsyncClient,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    r = await client.get("/api/metrics/summary", params=params or None)
    r.raise_for_status()
    row = r.json()
    return format_spending_summary_for_agent(row)


def format_spending_summary_for_agent(row: dict[str, Any]) -> dict[str, Any]:
    """Narrow API response to agent-facing numbers."""
    income = float(row.get("total_income") or 0)
    savings = float(row.get("total_savings") or 0)
    return {
        "status": "success",
        "period": {"from": row.get("date_from"), "to": row.get("date_to")},
        "income": income,
        "total_expense": float(row.get("total_expense") or 0),
        "savings": savings,
        "net": float(row.get("net") or 0),
        "savings_rate_pct": float(row.get("savings_rate") or 0),
        "transaction_count": int(row.get("txn_count") or 0),
        "currency": "INR",
    }


def _format_large_transaction_rows(txns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Single-txn rows for a month — includes merchant/counterparty label (not user PII)."""
    slim: list[dict[str, Any]] = []
    for t in txns:
        slim.append(
            {
                "txn_date": t.get("txn_date"),
                "amount": float(t.get("amount") or 0),
                "direction": t.get("direction"),
                "counterparty": t.get("counterparty"),
                "counterparty_category": t.get("counterparty_category"),
                "spend_category": t.get("spend_category"),
                "txn_type": t.get("txn_type"),
            }
        )
    return slim


@tool(
    name="get_spending_by_category",
    description=(
        "Break down spending (or income) by counterparty_category for a date range, with optional "
        "add-on: large individual transactions in a **calendar month** (``large_txn_year_month`` "
        "YYYY-MM, default current month) above ``large_txn_threshold`` INR — same data as the old "
        "top-expenses path, merged here to avoid redundant tools. "
        "Default direction is OUTFLOW (expenses). "
        "For headline income/expense/savings rate for one window, use get_spending_summary."
    ),
)
async def get_spending_by_category(
    client: AsyncClient,
    date_from: str | None = None,
    date_to: str | None = None,
    direction: str | None = "OUTFLOW",
    include_large_transactions: bool | None = False,
    large_txn_threshold: float | None = 5000.0,
    large_txn_year_month: str | None = None,
    large_txn_limit: int | None = 30,
) -> dict[str, Any]:
    params: dict[str, str] = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if direction:
        params["direction"] = direction.upper()
    r = await client.get("/api/metrics/by-category", params=params or None)
    r.raise_for_status()
    rows: list[dict[str, Any]] = r.json()

    large_slice: list[dict[str, Any]] | None = None
    if include_large_transactions:
        thr = 5000.0 if large_txn_threshold is None else float(large_txn_threshold)
        lim = 30 if large_txn_limit is None else max(1, min(100, int(large_txn_limit)))
        tp: dict[str, str] = {"threshold": str(thr)}
        if large_txn_year_month:
            tp["year_month"] = large_txn_year_month
        tr = await client.get("/api/metrics/top-expenses", params=tp)
        tr.raise_for_status()
        raw: list[dict[str, Any]] = tr.json()
        large_slice = _format_large_transaction_rows(raw[:lim])

    return format_spending_by_category_for_agent(rows, large_transactions=large_slice)


def format_spending_by_category_for_agent(
    rows: list[dict[str, Any]],
    *,
    large_transactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add rank and cumulative coverage; optionally attach large-txn rows."""
    out_rows: list[dict[str, Any]] = []
    total_pct = 0.0
    for i, row in enumerate(rows, start=1):
        pct = float(row.get("percentage") or 0)
        total_pct += pct
        out_rows.append(
            {
                "rank": i,
                "category": row.get("category"),
                "amount": float(row.get("amount") or 0),
                "percentage": pct,
                "txn_count": int(row.get("txn_count") or 0),
                "cumulative_percentage_top_n": round(total_pct, 2),
            }
        )
    top3 = sum(float(r.get("percentage") or 0) for r in rows[:3])
    out: dict[str, Any] = {
        "status": "success",
        "categories": out_rows,
        "category_count": len(rows),
        "top_3_categories_share_pct": round(top3, 2),
        "currency": "INR",
    }
    if large_transactions is not None:
        out["large_transactions"] = large_transactions
        out["large_transaction_count"] = len(large_transactions)
    return out


@tool(
    name="get_spending_trends",
    description=(
        "Month-by-month income, expense, net, and savings_rate for the last N months. "
        "Use for trends, multi-month savings rate, or 'how does this month compare'. "
        "For a single custom date range, use get_spending_summary instead."
    ),
)
async def get_spending_trends(
    client: AsyncClient,
    months: int | None = 6,
) -> dict[str, Any]:
    m = 6 if months is None else max(1, min(36, int(months)))
    r = await client.get("/api/metrics/monthly-trend", params={"months": str(m)})
    r.raise_for_status()
    rows: list[dict[str, Any]] = r.json()
    return format_spending_trends_for_agent(rows)


def format_spending_trends_for_agent(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive simple trend labels from the last few months' savings_rate."""
    series = [
        {
            "month": row.get("month"),
            "income": float(row.get("income") or 0),
            "expense": float(row.get("expense") or 0),
            "net": float(row.get("net") or 0),
            "savings_rate": float(row.get("savings_rate") or 0),
        }
        for row in rows
    ]
    rates = [s["savings_rate"] for s in series[-3:]]
    avg = sum(rates) / len(rates) if rates else 0.0
    trend_direction = "stable"
    if len(rates) >= 2:
        if rates[-1] > rates[0] + 1.0:
            trend_direction = "improving"
        elif rates[-1] < rates[0] - 1.0:
            trend_direction = "declining"
    return {
        "status": "success",
        "months": series,
        "avg_savings_rate_last_3": round(avg, 2),
        "trend_direction": trend_direction,
        "currency": "INR",
    }


@tool(
    name="get_recurring_expenses",
    description=(
        "Recurring OUTFLOW patterns (subscriptions, bills, EMIs): monthly-equivalent amounts, "
        "merchant/counterparty labels, and category. Includes headline totals from "
        "/api/recurring/summary. For one-off transaction search, use search_transactions."
    ),
)
async def get_recurring_expenses(client: AsyncClient) -> dict[str, Any]:
    s = await client.get("/api/recurring/summary")
    s.raise_for_status()
    summary = s.json()

    p = await client.get(
        "/api/recurring",
        params={"direction": "OUTFLOW", "is_active": True},
    )
    p.raise_for_status()
    patterns: list[dict[str, Any]] = p.json()
    return format_recurring_expenses_for_agent(summary, patterns)


def format_recurring_expenses_for_agent(
    summary: dict[str, Any],
    patterns: list[dict[str, Any]],
) -> dict[str, Any]:
    slim_patterns: list[dict[str, Any]] = []
    for row in patterns:
        slim_patterns.append(
            {
                "counterparty": row.get("counterparty"),
                "counterparty_category": row.get("counterparty_category"),
                "expected_amount": float(row.get("expected_amount") or 0),
                "frequency": row.get("frequency"),
                "next_expected_date": row.get("next_expected_date"),
            }
        )
    return {
        "status": "success",
        "total_monthly_fixed_cost": float(summary.get("total_monthly_fixed_cost") or 0),
        "total_monthly_recurring_income": float(summary.get("total_monthly_recurring_income") or 0),
        "active_pattern_count": int(summary.get("active_pattern_count") or 0),
        "patterns_due_this_week": int(summary.get("patterns_due_this_week") or 0),
        "active_outflow_patterns": slim_patterns,
        "currency": "INR",
    }


@tool(
    name="search_transactions",
    description=(
        "Search and list bank/card transactions with filters and pagination. "
        "Includes **counterparty** (merchant / payee label — not the user's personal id). "
        "category is counterparty_category (exact). "
        "sort_by: txn_date (default), amount, or created_at; sort_order asc or desc — "
        "use amount + desc for 'largest spends first'. "
        "For category rollups, use get_spending_by_category; for headline totals, get_spending_summary."
    ),
)
async def search_transactions(
    client: AsyncClient,
    search: str | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    direction: str | None = None,
    sort_by: str | None = "txn_date",
    sort_order: str | None = "desc",
    page_size: int | None = 20,
    page: int | None = 1,
) -> dict[str, Any]:
    ps = 20 if page_size is None else max(1, min(200, int(page_size)))
    pg = 1 if page is None else max(1, int(page))
    params: dict[str, str] = {"page": str(pg), "page_size": str(ps)}
    if search:
        params["search"] = search
    if category:
        params["category"] = category
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if direction:
        params["direction"] = direction.upper()
    sb = (sort_by or "txn_date").strip().lower()
    if sb in ("txn_date", "amount", "created_at", "counterparty"):
        params["sort_by"] = sb
    so = (sort_order or "desc").strip().lower()
    if so in ("asc", "desc"):
        params["sort_order"] = so
    r = await client.get("/api/transactions", params=params)
    r.raise_for_status()
    body: dict[str, Any] = r.json()
    return format_search_transactions_for_agent(body)


def format_search_transactions_for_agent(body: dict[str, Any]) -> dict[str, Any]:
    items_in = body.get("items") or []
    slim: list[dict[str, Any]] = []
    for t in items_in:
        slim.append(
            {
                "id": t.get("id"),
                "txn_date": t.get("txn_date"),
                "amount": float(t.get("amount") or 0),
                "direction": t.get("direction"),
                "counterparty": t.get("counterparty"),
                "counterparty_category": t.get("counterparty_category"),
                "spend_category": t.get("spend_category"),
                "txn_type": t.get("txn_type"),
            }
        )
    return {
        "status": "success",
        "items": slim,
        "total": int(body.get("total") or 0),
        "page": int(body.get("page") or 1),
        "page_size": int(body.get("page_size") or 0),
        "total_pages": int(body.get("total_pages") or 1),
        "currency": "INR",
    }
