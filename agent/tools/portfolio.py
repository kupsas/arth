"""
Portfolio / net-worth tools — facades over ``/api/holdings/*`` and ``/api/investment-transactions``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from httpx import AsyncClient

from agent.tools.base import tool


@tool(
    name="get_net_worth",
    description=(
        "Get current net worth: assets minus liabilities, plus asset allocation, "
        "concentration metrics, and portfolio-level totals from holdings. "
        "Amounts are **INR**; on success use ``net_worth.total`` and ``total_portfolio_value_inr`` "
        "for headline totals (they mirror the same snapshot when both are present). "
        "Use when the user asks about net worth, total assets, liabilities, or "
        "high-level allocation. Optional as_of date (YYYY-MM-DD) for a historical snapshot. "
        "For per-holding weights and returns, use get_holdings_breakdown."
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
    """Portfolio + net-worth snapshot (totals and allocation are not user PII)."""
    nw = body.get("net_worth") or {}
    alloc = body.get("allocation") or {}
    conc = body.get("concentration") or {}
    breakdown = body.get("asset_class_breakdown") or {}
    tpv = body.get("total_portfolio_value")
    return {
        "status": "success",
        "net_worth": {
            "total": nw.get("total"),
            "assets": nw.get("assets"),
            "liabilities": nw.get("liabilities"),
        },
        "allocation_by_asset_class": alloc,
        "concentration": conc,
        # Explicit duplicate for LLMs that key off clearer naming
        "total_portfolio_value": tpv,
        "total_portfolio_value_inr": tpv,
        "total_cost_basis": body.get("total_cost_basis"),
        "total_overall_gain": body.get("total_overall_gain"),
        "total_overall_gain_pct": body.get("total_overall_gain_pct"),
        "asset_class_breakdown": breakdown,
        "currency": "INR",
    }


@tool(
    name="get_holdings_breakdown",
    description=(
        "List holdings with **name**, symbol, value, weight, gains, liquidity, platform, and "
        "enriched metadata (sector, market_cap_class, fund_category, fund_house, fund_type) plus "
        "return metrics. Use for which position is doing well, sleeve mix, or MF vs equity. "
        "For headline net worth only, use get_net_worth."
    ),
)
async def get_holdings_breakdown(
    client: AsyncClient,
    asset_class: str | None = None,
    liquidity_class: str | None = None,
) -> dict[str, Any]:
    params: dict[str, str] = {}
    if asset_class:
        params["asset_class"] = asset_class
    if liquidity_class:
        params["liquidity_class"] = liquidity_class
    h = await client.get("/api/holdings", params=params or None)
    h.raise_for_status()
    holdings: list[dict[str, Any]] = h.json()

    br = await client.get("/api/holdings/batch-returns")
    br.raise_for_status()
    returns_map: dict[str, Any] = (br.json().get("returns") or {})

    return format_holdings_breakdown_for_agent(holdings, returns_map)


def format_holdings_breakdown_for_agent(
    holdings: list[dict[str, Any]],
    returns_map: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total_val = 0.0
    for row in holdings:
        hid = row.get("id")
        ret = returns_map.get(str(hid)) if hid is not None else None
        cv = float(row.get("current_value") or 0)
        total_val += cv
        rblock: dict[str, Any] = {}
        if isinstance(ret, dict):
            for k in ("xirr", "cagr", "absolute_return", "message", "method"):
                if k in ret:
                    rblock[k] = ret.get(k)
        rows.append(
            {
                "holding_id": hid,
                "name": row.get("name"),
                "symbol": row.get("symbol"),
                "asset_class": row.get("asset_class"),
                "liquidity_class": row.get("liquidity_class"),
                "account_platform": row.get("account_platform"),
                "sector": row.get("sector"),
                "market_cap_class": row.get("market_cap_class"),
                "fund_category": row.get("fund_category"),
                "fund_house": row.get("fund_house"),
                "fund_type": row.get("fund_type"),
                "current_value": cv,
                "weight_pct": float(row.get("weight_pct") or 0),
                "overall_gain": row.get("overall_gain"),
                "overall_gain_pct": row.get("overall_gain_pct"),
                "valuation_method": row.get("valuation_method"),
                "returns": rblock or None,
            }
        )
    return {
        "status": "success",
        "holdings": rows,
        "holding_count": len(rows),
        "total_current_value": round(total_val, 2),
        "currency": "INR",
    }


@tool(
    name="get_net_worth_trend",
    description=(
        "Net worth over time between two dates (daily / weekly / monthly anchors). "
        "Use for wealth trajectory questions. Defaults to roughly the last 12 months if "
        "dates are omitted. For today's snapshot only, use get_net_worth."
    ),
)
async def get_net_worth_trend(
    client: AsyncClient,
    start_date: str | None = None,
    end_date: str | None = None,
    granularity: str | None = "monthly",
) -> dict[str, Any]:
    today = dt.date.today()
    try:
        if end_date:
            end = dt.date.fromisoformat(end_date[:10])
        else:
            end = today
        if start_date:
            start = dt.date.fromisoformat(start_date[:10])
        else:
            start = end - dt.timedelta(days=365)
    except ValueError:
        return {
            "status": "error",
            "error": "invalid_date",
            "detail": "Use YYYY-MM-DD for start_date and end_date.",
        }
    if start > end:
        return {
            "status": "error",
            "error": "invalid_date_range",
            "detail": "start_date must be on or before end_date.",
        }
    gran = (granularity or "monthly").lower()
    if gran not in ("daily", "weekly", "monthly"):
        gran = "monthly"
    r = await client.get(
        "/api/holdings/history",
        params={
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "granularity": gran,
        },
    )
    r.raise_for_status()
    body = r.json()
    return format_net_worth_trend_for_agent(body)


def format_net_worth_trend_for_agent(body: dict[str, Any]) -> dict[str, Any]:
    pts = body.get("points") or []
    series = [
        {
            "date": p.get("date"),
            "net_worth": float(p.get("net_worth") or 0),
            "total_assets": float(p.get("total_assets") or 0),
            "total_liabilities": float(p.get("total_liabilities") or 0),
        }
        for p in pts
    ]
    direction = "flat"
    overall_change_pct = 0.0
    if len(series) >= 2:
        a = series[0]["net_worth"]
        b = series[-1]["net_worth"]
        if a and abs(a) > 1e-6:
            overall_change_pct = round((b - a) / abs(a) * 100, 2)
        if b > a * 1.02:
            direction = "growing"
        elif b < a * 0.98:
            direction = "declining"
    return {
        "status": "success",
        "granularity": body.get("granularity"),
        "points": series,
        "overall_change_pct": overall_change_pct,
        "direction": direction,
        "currency": "INR",
    }


@tool(
    name="get_investment_activity",
    description=(
        "Paginated investment transactions (BUY, SIP, SELL, DIVIDEND, switches). "
        "Use for 'how much invested this year', SIP history, dividends. "
        "Filters: date_from/date_to (YYYY-MM-DD), txn_type (e.g. SIP, BUY). "
        "Does not expose holding names. For bank/card spend, use search_transactions."
    ),
)
async def get_investment_activity(
    client: AsyncClient,
    date_from: str | None = None,
    date_to: str | None = None,
    txn_type: str | None = None,
    page_size: int | None = 20,
    page: int | None = 1,
) -> dict[str, Any]:
    ps = 20 if page_size is None else max(1, min(500, int(page_size)))
    pg = 1 if page is None else max(1, int(page))
    params: dict[str, str] = {"page": str(pg), "page_size": str(ps)}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if txn_type:
        params["txn_type"] = txn_type.upper()
    r = await client.get("/api/investment-transactions", params=params)
    r.raise_for_status()
    body: dict[str, Any] = r.json()
    return format_investment_activity_for_agent(body)


def format_investment_activity_for_agent(body: dict[str, Any]) -> dict[str, Any]:
    items_in = body.get("items") or []
    by_type: dict[str, int] = {}
    totals_inr_by_txn_type: dict[str, float] = {}
    slim: list[dict[str, Any]] = []
    for t in items_in:
        tt = str(t.get("txn_type") or "")
        by_type[tt] = by_type.get(tt, 0) + 1
        amt = float(t.get("total_amount") or 0)
        totals_inr_by_txn_type[tt] = totals_inr_by_txn_type.get(tt, 0.0) + amt
        slim.append(
            {
                "id": t.get("id"),
                "txn_date": str(t.get("txn_date")),
                "txn_type": tt,
                "total_amount": amt,
                "quantity": float(t.get("quantity") or 0),
                "symbol": t.get("symbol"),
                "account_platform": t.get("account_platform"),
            }
        )
    return {
        "status": "success",
        "items": slim,
        "summary_count_by_txn_type": by_type,
        "total_inr_by_txn_type": {k: round(v, 2) for k, v in totals_inr_by_txn_type.items()},
        "total": int(body.get("total") or 0),
        "page": int(body.get("page") or 1),
        "page_size": int(body.get("page_size") or 0),
        "total_pages": int(body.get("total_pages") or 1),
        "currency": "INR",
    }
