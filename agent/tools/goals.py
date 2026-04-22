"""
Goals tools — list, detail, surplus, and hierarchy via ``/api/goals*`` and ``/api/surplus``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from httpx import AsyncClient

from agent.tools._resolvers import resolve_goal
from agent.tools.base import tool

# Shown in tool payloads and descriptions so the model does not confuse PV vs future nominal.
GOAL_TARGET_AMOUNT_BASIS_NOTE = (
    "Goal target_amount values are in today's rupees (present-day / real terms as stored by Arth). "
    "They are not the future nominal lump sum at the goal date; compound or inflation math to a "
    "future nominal amount is not returned by these tools—derive that separately if needed."
)


@tool(
    name="get_goals_overview",
    description=(
        "List the user's financial goals with progress and fetch system priority scores. "
        "Filter by ``activation_status`` (default ACTIVE): PENDING, ACTIVE, COMPLETED, PAUSED. "
        "Use when the user asks about goals, goal order, which goal matters most, or "
        "progress toward named goals at a glance. Read-only. "
        "**Each goal's target_amount is in today's money (INR), not a future nominal value at the goal date.** "
        "For one goal's deep dive (ancestors, descendants), use get_goal_detail."
    ),
)
async def get_goals_overview(
    client: AsyncClient,
    activation_status: str | None = "ACTIVE",
) -> dict[str, Any]:
    """``activation_status`` matches API: PENDING | ACTIVE | COMPLETED | PAUSED."""
    params: dict[str, str] = {}
    if activation_status:
        params["activation_status"] = activation_status
    r = await client.get("/api/goals", params=params or None)
    r.raise_for_status()
    goals_raw: list[dict[str, Any]] = r.json()

    pr = await client.get("/api/goals/priorities", params={"persist": "false"})
    pr.raise_for_status()
    priorities = pr.json()

    return format_goals_overview_for_agent(goals_raw, priorities)


def format_goals_overview_for_agent(
    goals: list[dict[str, Any]],
    priorities: dict[str, Any],
) -> dict[str, Any]:
    slim_goals: list[dict[str, Any]] = []
    for g in goals:
        slim_goals.append(
            {
                "id": g.get("id"),
                "name": g.get("name"),
                "goal_type": g.get("goal_type"),
                "activation_status": g.get("activation_status"),
                "target_amount": g.get("target_amount"),
                "target_date": g.get("target_date"),
                "computed_percentage": g.get("computed_percentage"),
                "computed_current_value": g.get("computed_current_value"),
                "allocation_priority": g.get("allocation_priority"),
                "tier": g.get("tier"),
            }
        )
    pri_list = priorities.get("priorities") or []
    slim_pri = [
        {
            "goal_id": p.get("goal_id"),
            "goal_name": p.get("goal_name"),
            "priority_score": p.get("priority_score"),
            "suggested_rank": p.get("suggested_rank"),
        }
        for p in pri_list
    ]
    return {
        "status": "success",
        "target_amount_basis": GOAL_TARGET_AMOUNT_BASIS_NOTE,
        "goals": slim_goals,
        "priorities": slim_pri,
        "monthly_surplus_hint": priorities.get("monthly_surplus"),
        "active_goal_count": priorities.get("active_goal_count"),
    }


def _slim_goal_core(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": g.get("name"),
        "goal_type": g.get("goal_type"),
        "activation_status": g.get("activation_status"),
        "target_amount": g.get("target_amount"),
        "target_date": str(g.get("target_date")) if g.get("target_date") else None,
        "computed_percentage": g.get("computed_percentage"),
        "computed_current_value": g.get("computed_current_value"),
        "tier": g.get("tier"),
        "allocation_priority": g.get("allocation_priority"),
    }


def _months_remaining(target_date_str: str | None) -> int | None:
    if not target_date_str:
        return None
    try:
        td = dt.date.fromisoformat(str(target_date_str)[:10])
    except ValueError:
        return None
    today = dt.date.today()
    if td <= today:
        return 0
    return max(0, (td.year - today.year) * 12 + td.month - today.month)


@tool(
    name="get_goal_detail",
    description=(
        "Deep detail for **one** goal: progress, targets, tier, plus ancestor and descendant "
        "goal names (no graph ids). Accepts fuzzy ``goal_name_or_id`` "
        "(e.g. 'emergency fund', 'house'). Read-only. "
        "**target_amount is in today's money (INR), not the future nominal amount at target_date.** "
        "For all goals + system ranks, use get_goals_overview."
    ),
)
async def get_goal_detail(
    client: AsyncClient,
    goal_name_or_id: str,
) -> dict[str, Any]:
    resolved = await resolve_goal(client, goal_name_or_id)
    if resolved is None or resolved.get("id") is None:
        return {
            "status": "error",
            "error": "goal_not_found",
            "detail": f"No goal matched {goal_name_or_id!r}. Try a shorter distinctive phrase.",
        }
    gid = int(resolved["id"])

    g = await client.get(f"/api/goals/{gid}")
    g.raise_for_status()
    goal = g.json()

    an = await client.get(f"/api/goals/{gid}/ancestors")
    an.raise_for_status()
    ancestors = an.json()

    de = await client.get(f"/api/goals/{gid}/descendants")
    de.raise_for_status()
    descendants = de.json()

    return format_goal_detail_for_agent(goal, ancestors, descendants)


def format_goal_detail_for_agent(
    goal: dict[str, Any],
    ancestors: list[dict[str, Any]],
    descendants: list[dict[str, Any]],
) -> dict[str, Any]:
    td = str(goal.get("target_date")) if goal.get("target_date") else None
    monthly_need = goal.get("computed_monthly_need") or goal.get("monthly_need")

    anc_names = [
        {
            "name": a.get("name"),
            "computed_percentage": a.get("computed_percentage"),
        }
        for a in ancestors
    ]
    des_names = [
        {
            "name": d.get("name"),
            "computed_percentage": d.get("computed_percentage"),
        }
        for d in descendants
    ]
    return {
        "status": "success",
        "target_amount_basis": GOAL_TARGET_AMOUNT_BASIS_NOTE,
        "goal": _slim_goal_core(goal),
        "months_remaining": _months_remaining(td),
        "monthly_need_last_snapshot": monthly_need,
        "ancestors": anc_names,
        "descendants": des_names,
    }


@tool(
    name="get_surplus_allocation",
    description=(
        "Rolling surplus analysis: recurring income vs expenses, dual-path surplus (A/B), "
        "and per-month detail. Use when the user asks how much they can invest after bills, "
        "or how surplus was estimated. Omits raw recurring pattern rows (PII/noise). "
        "For simulation forward paths, use run_projection."
    ),
)
async def get_surplus_allocation(
    client: AsyncClient,
    months: int | None = 6,
) -> dict[str, Any]:
    m = 6 if months is None else max(3, min(12, int(months)))
    r = await client.get("/api/surplus", params={"months": str(m)})
    r.raise_for_status()
    data = r.json()
    return format_surplus_allocation_for_agent(data)


def format_surplus_allocation_for_agent(data: dict[str, Any]) -> dict[str, Any]:
    details = data.get("month_details") or []
    slim_months: list[dict[str, Any]] = []
    for row in details:
        if not isinstance(row, dict):
            continue
        slim_months.append(
            {
                "month": row.get("month"),
                "income": float(row.get("income") or 0),
                "expense_category_filtered": float(row.get("expense_category_filtered") or 0),
                "expense_need": float(row.get("expense_need") or 0),
                "expense_want": float(row.get("expense_want") or 0),
                "surplus_path_a": float(row.get("surplus_path_a") or 0),
                "surplus_path_b": float(row.get("surplus_path_b") or 0),
            }
        )
    return {
        "status": "success",
        "monthly_income": float(data.get("monthly_income") or 0),
        "monthly_expense_baseline": float(data.get("monthly_expense_baseline") or 0),
        "monthly_surplus": float(data.get("monthly_surplus") or 0),
        "surplus_path_a": float(data.get("surplus_path_a") or 0),
        "surplus_path_b": float(data.get("surplus_path_b") or 0),
        "months_analyzed": int(data.get("months_analyzed") or 0),
        "computation_method": data.get("computation_method"),
        "month_details": slim_months,
        "warnings": data.get("warnings") or [],
        "currency": "INR",
    }


@tool(
    name="get_goal_tree",
    description=(
        "Goal hierarchy (pyramid tiers L1–L4 + untiered) with progress % per goal. "
        "Omits database ids and link edges — names and progress only. "
        "When combining with other goal tools, remember **target amounts are in today's money (INR)**. "
        "Use for 'how do my goals relate'. For a flat priority-ordered list, use get_goals_overview."
    ),
)
async def get_goal_tree(client: AsyncClient) -> dict[str, Any]:
    r = await client.get("/api/goals/tree")
    r.raise_for_status()
    tree = r.json()
    return format_goal_tree_for_agent(tree)


def format_goal_tree_for_agent(tree: dict[str, Any]) -> dict[str, Any]:
    tiers_out: list[dict[str, Any]] = []
    for label in ("l1", "l2", "l3", "l4", "untiered"):
        goals = tree.get(label) or []
        tier_goals: list[dict[str, Any]] = []
        for g in goals:
            if not isinstance(g, dict):
                continue
            tier_goals.append(
                {
                    "name": g.get("name"),
                    "computed_percentage": g.get("computed_percentage"),
                    "goal_type": g.get("goal_type"),
                }
            )
        tiers_out.append({"tier": label.upper(), "goals": tier_goals})
    return {
        "status": "success",
        "target_amount_basis": GOAL_TARGET_AMOUNT_BASIS_NOTE,
        "tiers": tiers_out,
        "currency": "INR",
    }
