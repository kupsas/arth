"""
Portfolio / net-worth tools — facades over ``/api/holdings/summary``.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from agent.tools.base import tool


@tool(
    name="get_net_worth",
    description=(
        "Get current net worth: assets minus liabilities, plus asset allocation, "
        "concentration metrics, and portfolio-level totals from holdings. "
        "Use when the user asks about net worth, total assets, liabilities, or "
        "high-level allocation. Optional as_of date (YYYY-MM-DD) for a historical snapshot."
    ),
)
async def get_net_worth(
    client: AsyncClient,
    as_of: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {}
    if as_of:
        params["as_of"] = as_of
    r = await client.get("/api/holdings/summary", params=params or None)
    r.raise_for_status()
    body = r.json()
    return format_net_worth_for_agent(body)


def format_net_worth_for_agent(body: dict[str, Any]) -> dict[str, Any]:
    """Pick stable numeric fields; drop holding rows (names can echo PII)."""
    nw = body.get("net_worth") or {}
    alloc = body.get("allocation") or {}
    conc = body.get("concentration") or {}
    breakdown = body.get("asset_class_breakdown") or {}
    return {
        "status": "success",
        "net_worth": {
            "total": nw.get("total"),
            "assets": nw.get("assets"),
            "liabilities": nw.get("liabilities"),
        },
        "allocation_by_asset_class": alloc,
        "concentration": conc,
        "total_portfolio_value": body.get("total_portfolio_value"),
        "total_cost_basis": body.get("total_cost_basis"),
        "total_overall_gain": body.get("total_overall_gain"),
        "total_overall_gain_pct": body.get("total_overall_gain_pct"),
        "asset_class_breakdown": breakdown,
        "currency": "INR",
    }
