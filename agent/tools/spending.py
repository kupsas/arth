"""
Spending-domain tools — thin facades over ``/api/metrics/*``.
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
        "For category-level spending, a future tool will break down by category."
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
    """Narrow API response to agent-safe numbers (PII-free)."""
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
