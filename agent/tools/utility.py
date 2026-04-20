"""
Utility tools — cross-cutting snapshots and calendar context (minimal / no API).
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

from httpx import AsyncClient

from agent.tools.base import tool


def _indian_fy_bounds(today: dt.date) -> tuple[dt.date, dt.date, str]:
    """Financial year Apr 1 → Mar 31 (India convention)."""
    if today.month >= 4:
        start = dt.date(today.year, 4, 1)
        end = dt.date(today.year + 1, 3, 31)
        label = f"FY {today.year}-{str(today.year + 1)[-2:]}"
    else:
        start = dt.date(today.year - 1, 4, 1)
        end = dt.date(today.year, 3, 31)
        label = f"FY {today.year - 1}-{str(today.year)[-2:]}"
    return start, end, label


@tool(
    name="get_user_profile",
    description=(
        "Compact snapshot: net worth, recurring monthly income/surplus (from surplus model), "
        "debt ratio, active accounts with txns, and count of ACTIVE goals. "
        "Use for 'tell me about my finances' before deeper tool calls. "
        "No free-text PII — numbers and counts only."
    ),
)
async def get_user_profile(client: AsyncClient) -> dict[str, Any]:
    hs = await client.get("/api/holdings/summary")
    hs.raise_for_status()
    hbody = hs.json()

    su = await client.get("/api/surplus", params={"months": "6"})
    su.raise_for_status()
    sbody = su.json()

    li = await client.get("/api/liabilities/summary")
    li.raise_for_status()
    lbody = li.json()

    ac = await client.get("/api/metrics/accounts-summary")
    ac.raise_for_status()
    accounts = ac.json()

    gl = await client.get("/api/goals", params={"activation_status": "ACTIVE"})
    gl.raise_for_status()
    goals = gl.json()

    nw = (hbody.get("net_worth") or {}).get("total")
    return {
        "status": "success",
        "net_worth_total_inr": nw,
        "monthly_recurring_income_inr": float(sbody.get("monthly_income") or 0),
        "monthly_surplus_inr": float(sbody.get("monthly_surplus") or 0),
        "debt_to_asset_ratio": float(lbody.get("debt_to_asset_ratio") or 0),
        "active_liability_count": int(lbody.get("active_count") or 0),
        "accounts_with_transactions": len(accounts) if isinstance(accounts, list) else 0,
        "active_goal_count": len(goals) if isinstance(goals, list) else 0,
        "currency": "INR",
    }


@tool(
    name="get_date_context",
    description=(
        "Today's date, Indian FY label (Apr–Mar), days into / remaining in FY, "
        "and days left in the calendar month. Use when the user says 'this FY' or "
        "you need an unambiguous 'today' for period questions."
    ),
)
async def get_date_context(client: AsyncClient) -> dict[str, Any]:
    _ = client  # No HTTP — signature matches other tools for the executor.
    today = dt.date.today()
    fy_start, fy_end, fy_label = _indian_fy_bounds(today)
    days_into_fy = (today - fy_start).days + 1
    days_remaining_fy = (fy_end - today).days
    last_dom = calendar.monthrange(today.year, today.month)[1]
    days_remaining_in_month = last_dom - today.day
    month_name = calendar.month_name[today.month]
    return {
        "status": "success",
        "current_date": today.isoformat(),
        "current_month_name": month_name,
        "fiscal_year_label": fy_label,
        "fiscal_year_start": fy_start.isoformat(),
        "fiscal_year_end": fy_end.isoformat(),
        "days_into_fy": days_into_fy,
        "days_remaining_fy": max(0, days_remaining_fy),
        "days_remaining_in_month": max(0, days_remaining_in_month),
    }
