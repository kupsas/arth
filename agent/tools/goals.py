"""
Goals tools — list active goals and system priority scores.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from agent.tools.base import tool


@tool(
    name="get_goals_overview",
    description=(
        "List the user's financial goals with progress and fetch system priority scores. "
        "Filter by ``activation_status`` (default ACTIVE): PENDING, ACTIVE, COMPLETED, PAUSED. "
        "Use when the user asks about goals, goal order, which goal matters most, or "
        "progress toward named goals. Read-only."
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
                "status": g.get("status"),
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
        "goals": slim_goals,
        "priorities": slim_pri,
        "monthly_surplus_hint": priorities.get("monthly_surplus"),
        "active_goal_count": priorities.get("active_goal_count"),
    }
