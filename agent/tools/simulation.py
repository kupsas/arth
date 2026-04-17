"""
Simulation tools — forward projections and scenario compare via ``/api/simulate/*``.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from httpx import AsyncClient

from agent.tools._resolvers import resolve_goal
from agent.tools.base import tool


async def _post_from_current(
    client: AsyncClient,
    *,
    simulation_months: int,
    surplus_trailing_months: int = 6,
) -> dict[str, Any]:
    body = {
        "simulation_months": max(1, min(600, int(simulation_months))),
        "surplus_trailing_months": max(3, min(12, int(surplus_trailing_months))),
    }
    r = await client.post("/api/simulate/from-current", json=body)
    r.raise_for_status()
    return r.json()


def _nw_end(result: dict[str, Any]) -> float | None:
    nw = result.get("net_worth_projection") or []
    if not nw:
        return None
    last = nw[-1]
    if isinstance(last, dict):
        return float(last.get("total_value") or 0)
    return None


def _parse_date(d: Any) -> dt.date | None:
    if d is None:
        return None
    if isinstance(d, dt.date) and not isinstance(d, dt.datetime):
        return d
    s = str(d)[:10]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _months_between(anchor: dt.date | None, end: dt.date | None) -> int | None:
    if anchor is None or end is None:
        return None
    return max(0, (end.year - anchor.year) * 12 + end.month - anchor.month)


def format_run_projection_for_agent(
    fc: dict[str, Any],
    *,
    focus_goal_name: str | None,
    focus_exact: bool = True,
) -> dict[str, Any]:
    params = fc.get("params") or {}
    result = fc.get("result") or {}
    anchor = _parse_date(params.get("as_of_date")) or dt.date.today()
    projections_in = result.get("projections") or []
    projections_out: list[dict[str, Any]] = []
    for p in projections_in:
        if not isinstance(p, dict):
            continue
        name = str(p.get("goal_name") or "")
        if focus_goal_name:
            fn = focus_goal_name.strip().lower()
            ln = name.lower()
            if focus_exact:
                if ln != fn:
                    continue
            elif fn not in ln:
                continue
        comp = _parse_date(p.get("projected_completion_date"))
        months_to_completion = _months_between(anchor, comp)
        st = str(p.get("status") or "")
        on_track = st in ("ON_TRACK", "ACHIEVED")
        projections_out.append(
            {
                "goal_name": name,
                "status": st,
                "projected_completion_date": str(p.get("projected_completion_date"))
                if p.get("projected_completion_date")
                else None,
                "months_to_completion": months_to_completion,
                "projected_final_amount": float(p.get("projected_final_amount") or 0),
                "target_amount": p.get("target_amount"),
                "shortfall": float(p.get("shortfall") or 0),
                "on_track": on_track,
                "monthly_allocation": float(p.get("monthly_allocation") or 0),
            }
        )
    nw = result.get("net_worth_projection") or []
    nw_summary = None
    if nw:
        first = nw[0] if isinstance(nw[0], dict) else {}
        last = nw[-1] if isinstance(nw[-1], dict) else {}
        nw_summary = {
            "start_month": str(first.get("month")),
            "end_month": str(last.get("month")),
            "start_total_value": float(first.get("total_value") or 0),
            "end_total_value": float(last.get("total_value") or 0),
        }
    return {
        "status": "success",
        "simulation_months": params.get("simulation_months"),
        "monthly_surplus_assumption": params.get("monthly_surplus"),
        "general_inflation_rate_pct": params.get("general_inflation_rate"),
        "projections": projections_out,
        "net_worth_projection_summary": nw_summary,
        "warnings": result.get("warnings") or [],
        "currency": "INR",
    }


@tool(
    name="run_projection",
    description=(
        "Run a forward-looking simulation from the user's current ACTIVE goals and surplus. "
        "Use for 'am I on track', 'when might I finish this goal', or overall trajectory. "
        "Optional goal_name_or_id narrows the projection list to one goal name. "
        "For what-if tweaks to monthly surplus, use compare_scenarios or simulate_surplus_change."
    ),
)
async def run_projection(
    client: AsyncClient,
    months: int | None = 60,
    goal_name_or_id: str | None = None,
) -> dict[str, Any]:
    m = 60 if months is None else max(1, min(600, int(months)))
    fc = await _post_from_current(client, simulation_months=m)
    focus: str | None = None
    exact = True
    raw_g = (goal_name_or_id or "").strip()
    if raw_g:
        g = await resolve_goal(client, raw_g)
        if g and g.get("name"):
            focus = str(g["name"])
        else:
            focus = raw_g
            exact = False
    return format_run_projection_for_agent(fc, focus_goal_name=focus, focus_exact=exact)


def _variant_params(base: dict[str, Any], new_surplus: float) -> dict[str, Any]:
    v = copy.deepcopy(base)
    v["monthly_surplus"] = float(new_surplus)
    return v


async def _post_compare(
    client: AsyncClient,
    base: dict[str, Any],
    variants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    r = await client.post("/api/simulate/compare", json={"base": base, "variants": variants})
    r.raise_for_status()
    return r.json()


def format_compare_result_for_agent(
    base_result: dict[str, Any],
    compare_rows: list[dict[str, Any]],
    *,
    scenario_label: str,
) -> dict[str, Any]:
    base_nw_end = _nw_end(base_result) or 0.0
    row0 = compare_rows[0] if compare_rows else {}
    var_result = row0.get("result") or {}
    var_nw_end = _nw_end(var_result) or 0.0
    deltas_in = row0.get("deltas") or []
    deltas_out: list[dict[str, Any]] = []
    for d in deltas_in:
        if not isinstance(d, dict):
            continue
        deltas_out.append(
            {
                "goal_name": d.get("goal_name"),
                "base_completion": str(d.get("base_completion")) if d.get("base_completion") else None,
                "variant_completion": str(d.get("variant_completion"))
                if d.get("variant_completion")
                else None,
                "months_shifted": d.get("months_shifted"),
                "base_status": d.get("base_status"),
                "variant_status": d.get("variant_status"),
            }
        )
    return {
        "status": "success",
        "scenario": scenario_label,
        "net_worth_end_base": round(base_nw_end, 2),
        "net_worth_end_variant": round(var_nw_end, 2),
        "net_worth_end_delta": round(var_nw_end - base_nw_end, 2),
        "goal_deltas": deltas_out,
        "changes_from_base": row0.get("changes_from_base") or {},
        "currency": "INR",
    }


@tool(
    name="compare_scenarios",
    description=(
        "Compare the current baseline simulation to a variant where **monthly_surplus** "
        "changes by ``surplus_change_amount`` INR (positive = more to invest each month). "
        "``change_description`` is a short human label for logs only (e.g. 'cut dining 10k'). "
        "Requires non-zero surplus_change_amount. "
        "For setting surplus to an absolute level, use simulate_surplus_change."
    ),
)
async def compare_scenarios(
    client: AsyncClient,
    change_description: str | None = None,
    surplus_change_amount: int | None = None,
    months: int | None = 60,
) -> dict[str, Any]:
    if surplus_change_amount is None:
        return {
            "status": "error",
            "error": "missing_parameter",
            "detail": "surplus_change_amount is required (INR delta vs current model surplus).",
        }
    if int(surplus_change_amount) == 0:
        return {
            "status": "error",
            "error": "invalid_parameter",
            "detail": "surplus_change_amount must be non-zero.",
        }
    m = 60 if months is None else max(1, min(600, int(months)))
    fc = await _post_from_current(client, simulation_months=m)
    params = fc.get("params") or {}
    base_surplus = float(params.get("monthly_surplus") or 0)
    new_surplus = base_surplus + float(surplus_change_amount)
    variant = _variant_params(params, new_surplus)
    cmp = await _post_compare(client, params, [variant])
    base_res = fc.get("result") or {}
    label = (change_description or "").strip() or "surplus_change_compare"
    return format_compare_result_for_agent(base_res, cmp, scenario_label=label)


@tool(
    name="simulate_surplus_change",
    description=(
        "What-if on **monthly investable surplus**: either set an absolute ``new_monthly_surplus`` "
        "(INR/month) or apply a ``surplus_delta`` to the model's current surplus — not both. "
        "Returns the same style of comparison as compare_scenarios (goal completion shifts, "
        "net worth at horizon). Use for income shocks, sabbatical, or 'if I saved X more'."
    ),
)
async def simulate_surplus_change(
    client: AsyncClient,
    months: int | None = 60,
    new_monthly_surplus: float | None = None,
    surplus_delta: float | None = None,
) -> dict[str, Any]:
    has_new = new_monthly_surplus is not None
    has_delta = surplus_delta is not None
    if has_new and has_delta:
        return {
            "status": "error",
            "error": "ambiguous_parameters",
            "detail": "Pass only one of new_monthly_surplus or surplus_delta.",
        }
    if not has_new and not has_delta:
        return {
            "status": "error",
            "error": "missing_parameter",
            "detail": "Provide new_monthly_surplus (absolute INR) or surplus_delta (relative change).",
        }
    m = 60 if months is None else max(1, min(600, int(months)))
    fc = await _post_from_current(client, simulation_months=m)
    params = fc.get("params") or {}
    base_surplus = float(params.get("monthly_surplus") or 0)
    if has_new:
        new_s = float(new_monthly_surplus)
        label = f"absolute_surplus_{new_s:.0f}"
    else:
        new_s = base_surplus + float(surplus_delta)
        label = f"delta_surplus_{float(surplus_delta):+.0f}"
    variant = _variant_params(params, new_s)
    cmp = await _post_compare(client, params, [variant])
    base_res = fc.get("result") or {}
    return format_compare_result_for_agent(
        base_res,
        cmp,
        scenario_label=label,
    )
