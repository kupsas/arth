"""
Simulation API — Sub-Plan G.

``POST`` endpoints accept full sandbox state as JSON; no DB reads except
``/from-current``, which hydrates :class:`SimulationParams` from the logged-in
user's goals + surplus + inflation.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api.auth import get_current_user
from api.database import get_session
from api.models import Goal, UserSimulationSandboxPreferences
from api.services.inflation_service import cpi_general_yoy_ema_pct, resolve_goal_inflation
from api.services.priority_scorer import _effective_goal_class, compute_priority_scores
from api.services.simulation import (
    SimulationGoal,
    SimulationParams,
    SimulationResult,
    allocate_surplus,
    compare_scenarios,
    simulate,
)
from api.services.surplus_calculator import compute_surplus

logger = logging.getLogger(__name__)

router = APIRouter()

# When ``compute_surplus`` yields no positive monthly flow yet, the simulation UI still
# needs a usable starting point (charts and allocation previews are meaningless at ₹0).
# ₹1,50,000/month — applied only in ``POST /from-current``, not in ``build_simulation_params_from_db``
# (goal status cache continues to use the raw computed surplus).
SANDBOX_DEFAULT_MONTHLY_SURPLUS_INR = 150_000.0
# Matches dashboard ``SIMULATION_MONTHLY_SURPLUS_MAX_INR`` (₹10 lakh / mo).
SANDBOX_MONTHLY_SURPLUS_MAX_INR = 1_000_000.0


def _apply_saved_sandbox_macros(
    session: Session,
    user_id: str,
    params: SimulationParams,
    meta: dict[str, Any],
) -> SimulationParams:
    """If the user saved simulate-page sliders, override computed surplus + headline CPI."""
    row = session.exec(
        select(UserSimulationSandboxPreferences).where(
            UserSimulationSandboxPreferences.user_id == user_id
        )
    ).first()
    if row is None:
        return params
    meta["sandbox_saved_macros_applied"] = True
    return params.model_copy(
        update={
            "monthly_surplus": max(
                0.0,
                min(float(row.monthly_surplus_inr), SANDBOX_MONTHLY_SURPLUS_MAX_INR),
            ),
            "salary_growth_rate": max(0.0, min(float(row.salary_growth_rate_pct), 50.0)),
            "general_inflation_rate": max(0.0, min(float(row.general_inflation_rate_pct), 50.0)),
        },
    )


class AllocateRequest(BaseModel):
    """Body for ``POST /allocate``."""

    goals: list[SimulationGoal]
    surplus: float = Field(..., description="Monthly INR to allocate")
    as_of_date: datetime.date | None = Field(
        default=None,
        description="Reference month for recurring windows (defaults to today)",
    )
    general_inflation_rate: float = Field(
        default=6.0,
        description="Headline CPI % — used when a goal's inflation_rate is null (same as simulate).",
    )
    salary_growth_rate: float = Field(
        default=0.0,
        ge=0,
        le=50,
        description="Annual % — when positive, PMT uses growing-annuity math (same as simulate).",
    )


class CompareRequest(BaseModel):
    """Body for ``POST /compare``."""

    base: SimulationParams
    variants: list[SimulationParams] = Field(default_factory=list)


def _goal_to_simulation_goal(
    session: Session,
    goal: Goal,
    rank_by_goal_id: dict[int, int],
) -> SimulationGoal:
    """Map a persisted :class:`Goal` to API simulation input."""
    gclass = _effective_goal_class(goal)
    alloc = goal.allocation_priority
    if alloc is None or alloc <= 0:
        alloc = rank_by_goal_id.get(goal.id or 0, 99)

    res_inf = resolve_goal_inflation(session, goal)
    exp_ret = goal.expected_return_rate
    if exp_ret is None:
        exp_ret = 10.0

    start_bal = goal.starting_balance
    if start_bal is None:
        start_bal = float(goal.current_value or 0.0)

    return SimulationGoal(
        id=goal.id,
        name=goal.name,
        goal_class=gclass,
        target_amount=goal.target_amount,
        target_date=goal.target_date,
        starting_balance=float(start_bal),
        allocation_priority=int(alloc),
        expected_return_rate=float(exp_ret),
        inflation_rate=float(res_inf["annual_pct"]),
        inflation_category=res_inf.get("category"),
        inflation_method=res_inf.get("method"),
        inflation_label=res_inf.get("label"),
        recurrence_amount=goal.recurrence_amount,
        recurrence_frequency=goal.recurrence_frequency,
        recurrence_start=goal.recurrence_start,
        recurrence_end=goal.recurrence_end,
        goal_subtype=goal.goal_subtype,
    )


def build_simulation_params_from_db(
    session: Session,
    user_id: str,
    *,
    simulation_months: int = 240,
    surplus_trailing_months: int = 6,
    as_of_date: datetime.date | None = None,
) -> tuple[SimulationParams, dict[str, Any]]:
    """Hydrate :class:`SimulationParams` from DB state for the sandbox page."""

    surplus_res = compute_surplus(session, user_id, months=surplus_trailing_months)
    pri = compute_priority_scores(session, user_id, persist=False)
    rank_by_goal_id = {p.goal_id: p.suggested_rank for p in pri.priorities}

    stmt = select(Goal).where(
        Goal.user_id == user_id,
        Goal.activation_status == "ACTIVE",
    )
    rows = list(session.exec(stmt).all())

    sim_goals = [_goal_to_simulation_goal(session, g, rank_by_goal_id) for g in rows]

    general_ema = cpi_general_yoy_ema_pct(session)

    meta: dict[str, Any] = {
        "user_id": user_id,
        "monthly_surplus_source": surplus_res.computation_method,
        "monthly_surplus_source_label": surplus_res.computation_method_label,
        "priority_computed_at": pri.computed_at.isoformat(),
        "simulation_inflation_ema_pct": general_ema,
        "active_goals_loaded": len(sim_goals),
    }

    params = SimulationParams(
        goals=sim_goals,
        monthly_surplus=float(surplus_res.monthly_surplus),
        general_inflation_rate=float(general_ema),
        simulation_months=simulation_months,
        as_of_date=as_of_date or datetime.date.today(),
    )
    params = _apply_saved_sandbox_macros(session, user_id, params, meta)
    return params, meta


@router.post("", response_model=SimulationResult)
def post_simulate(body: SimulationParams) -> SimulationResult:
    """Run a full month-by-month simulation from JSON state (no DB)."""
    return simulate(body)


@router.post("/compare")
def post_compare(body: CompareRequest) -> list[dict[str, Any]]:
    """Compare base params to one or more variants."""
    results = compare_scenarios(body.base, body.variants)
    return [r.model_dump() for r in results]


@router.post("/allocate")
def post_allocate(body: AllocateRequest) -> dict[str, float]:
    """Priority waterfall allocation for one month (no full projection)."""
    return allocate_surplus(
        body.goals,
        body.surplus,
        today=body.as_of_date,
        general_inflation_rate=body.general_inflation_rate,
        salary_growth_rate=body.salary_growth_rate,
    )


class FromCurrentBody(BaseModel):
    """Optional overrides for ``POST /from-current``."""

    simulation_months: int = Field(default=240, ge=1, le=600)
    surplus_trailing_months: int = Field(default=6, ge=3, le=12)
    as_of_date: datetime.date | None = None


class SandboxPreferencesPut(BaseModel):
    """Persisted simulate-page sliders (dashboard Save changes)."""

    monthly_surplus: float = Field(..., ge=0, le=SANDBOX_MONTHLY_SURPLUS_MAX_INR)
    salary_growth_rate: float = Field(default=5.0, ge=0, le=50)
    general_inflation_rate: float = Field(default=6.0, ge=0, le=50)


@router.put("/sandbox-preferences")
def put_simulation_sandbox_preferences(
    body: SandboxPreferencesPut,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(get_current_user),
) -> dict[str, bool]:
    """Upsert saved monthly surplus, salary growth, and headline inflation for simulation."""
    now = datetime.datetime.now(datetime.UTC)
    ms = max(0.0, min(float(body.monthly_surplus), SANDBOX_MONTHLY_SURPLUS_MAX_INR))
    sg = max(0.0, min(float(body.salary_growth_rate), 50.0))
    gi = max(0.0, min(float(body.general_inflation_rate), 50.0))
    row = session.exec(
        select(UserSimulationSandboxPreferences).where(
            UserSimulationSandboxPreferences.user_id == user_id
        )
    ).first()
    if row is None:
        row = UserSimulationSandboxPreferences(
            user_id=user_id,
            monthly_surplus_inr=ms,
            salary_growth_rate_pct=sg,
            general_inflation_rate_pct=gi,
        )
    else:
        row.monthly_surplus_inr = ms
        row.salary_growth_rate_pct = sg
        row.general_inflation_rate_pct = gi
    row.updated_at = now
    session.add(row)
    session.commit()
    return {"ok": True}


@router.post("/from-current")
def post_simulate_from_current(
    body: FromCurrentBody | None = None,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Load ACTIVE goals + surplus + inflation, then run :func:`simulate`."""
    opts = body or FromCurrentBody()
    params, meta = build_simulation_params_from_db(
        session,
        user_id,
        simulation_months=opts.simulation_months,
        surplus_trailing_months=opts.surplus_trailing_months,
        as_of_date=opts.as_of_date,
    )
    if params.monthly_surplus <= 0:
        params = params.model_copy(
            update={"monthly_surplus": SANDBOX_DEFAULT_MONTHLY_SURPLUS_INR}
        )
    result = simulate(params)
    logger.debug(
        "Simulation from-current finished goals=%s horizon_months=%s",
        len(params.goals),
        opts.simulation_months,
    )
    return {
        "params": params.model_dump(),
        "meta": meta,
        "result": result.model_dump(),
    }
