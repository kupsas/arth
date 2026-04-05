"""
Goals architecture Sub-Plan G — pure-function simulation engine.

No database access inside :func:`simulate`. Callers map DB rows to :class:`SimulationGoal`
and pass :class:`SimulationParams`; results are JSON-serializable Pydantic models.

See ``docs/personal-data/goals-architecture-master-plan.md`` § Sub-Plan G.
"""

from __future__ import annotations

import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from api.services.goal_decomposer import add_months, months_between

# ── Goal class constants (aligned with pipeline / API) ───────────────────────
GC_POINT = "POINT_IN_TIME"
GC_RECURRING = "RECURRING_CASH_FLOW"
GC_GROWTH = "GROWTH"

GoalSimStatus = Literal["ON_TRACK", "AT_RISK", "BEHIND", "ACHIEVED", "IMPOSSIBLE"]


# ── Input models ──────────────────────────────────────────────────────────────


class OneTimeEvent(BaseModel):
    """A lump-sum cash event in a specific calendar month."""

    amount: float = Field(..., description="Positive INR; direction is inflow vs outflow list")
    date: datetime.date
    description: str = ""


class SimulationGoal(BaseModel):
    """Sandbox snapshot of a goal — may differ from persisted DB rows."""

    id: int | None = None
    name: str
    goal_class: str = Field(
        ...,
        description="POINT_IN_TIME | RECURRING_CASH_FLOW | GROWTH",
    )
    target_amount: float | None = None
    target_date: datetime.date | None = None
    starting_balance: float = 0.0
    allocation_priority: int = Field(
        default=99,
        ge=1,
        description="Lower number = fund first (1 = highest priority)",
    )
    expected_return_rate: float = Field(
        default=10.0,
        ge=0,
        le=50,
        description="Nominal annual % for this goal's pot",
    )
    inflation_rate: float | None = Field(
        default=None,
        ge=0,
        le=50,
        description=(
            "Annual % — inflates POINT_IN_TIME target over simulation time. "
            "None uses SimulationParams.general_inflation_rate (headline CPI in production)."
        ),
    )
    recurrence_amount: float | None = None
    recurrence_frequency: str | None = Field(
        default=None,
        description="MONTHLY | QUARTERLY | ANNUAL — amounts are per period unless noted",
    )
    recurrence_start: datetime.date | None = None
    recurrence_end: datetime.date | None = None
    goal_subtype: str | None = None


class SimulationParams(BaseModel):
    """Full input to one :func:`simulate` run."""

    goals: list[SimulationGoal] = Field(default_factory=list)
    monthly_surplus: float = Field(default=0.0, description="Base monthly investable surplus (INR)")
    salary_growth_rate: float = Field(
        default=5.0,
        ge=0,
        le=50,
        description="Annual % raise applied to monthly_surplus every 12 simulated months",
    )
    general_inflation_rate: float = Field(
        default=6.0,
        description="Headline annual % — used when SimulationGoal.inflation_rate is None (R-INF-1 / S4.4).",
    )
    simulation_months: int = Field(default=240, ge=1, le=600)
    one_time_inflows: list[OneTimeEvent] = Field(default_factory=list)
    one_time_outflows: list[OneTimeEvent] = Field(default_factory=list)
    as_of_date: datetime.date | None = Field(
        default=None,
        description="Simulation start anchor (first month = this calendar month). Defaults to today.",
    )


# ── Output models ─────────────────────────────────────────────────────────────


class MonthlySnapshot(BaseModel):
    month: datetime.date
    cumulative_value: float
    monthly_contribution: float
    monthly_return: float
    target_at_month: float | None = None


class GoalProjection(BaseModel):
    goal_id: int | None
    goal_name: str
    monthly_allocation: float = Field(
        ...,
        description="Average monthly INR allocated to this goal over the horizon",
    )
    projected_completion_date: datetime.date | None = None
    status: GoalSimStatus
    projected_final_amount: float
    shortfall: float = Field(
        ...,
        description="Positive if behind nominal target at end (POINT_IN_TIME); 0 if on track",
    )
    monthly_trajectory: list[MonthlySnapshot] = Field(default_factory=list)


class CascadeEvent(BaseModel):
    month: datetime.date
    completed_goal: str
    freed_surplus: float
    beneficiary_goals: list[str] = Field(default_factory=list)


class MonthlyNetWorth(BaseModel):
    month: datetime.date
    total_value: float
    total_contributions: float
    total_returns: float


class SimulationResult(BaseModel):
    projections: list[GoalProjection]
    surplus_allocation: dict[str, float] = Field(
        default_factory=dict,
        description="goal_name -> average monthly INR allocated",
    )
    total_surplus_allocated: float = 0.0
    unallocated_surplus: float = 0.0
    cascade_events: list[CascadeEvent] = Field(default_factory=list)
    net_worth_projection: list[MonthlyNetWorth] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GoalDelta(BaseModel):
    goal_name: str
    base_completion: datetime.date | None = None
    variant_completion: datetime.date | None = None
    base_status: GoalSimStatus | None = None
    variant_status: GoalSimStatus | None = None
    months_shifted: int | None = Field(
        None,
        description="Negative = variant completes earlier (in months)",
    )


class ScenarioComparison(BaseModel):
    scenario_name: str
    changes_from_base: dict[str, Any] = Field(default_factory=dict)
    result: SimulationResult
    deltas: list[GoalDelta] = Field(default_factory=list)


# ── Internal helpers ───────────────────────────────────────────────────────────


def _effective_goal_inflation(goal: SimulationGoal, general_inflation_pct: float) -> float:
    """Annual inflation % for POINT_IN_TIME targets. None → headline/general (CSV R-INF-1)."""
    if goal.inflation_rate is None:
        return float(general_inflation_pct)
    return float(goal.inflation_rate)


def _monthly_r(annual_pct: float) -> float:
    return annual_pct / 12.0 / 100.0


def _recurring_monthly_need(g: SimulationGoal) -> float:
    """Convert recurrence_amount to an average monthly INR need while active."""
    if g.recurrence_amount is None or g.recurrence_amount <= 0:
        return 0.0
    freq = (g.recurrence_frequency or "MONTHLY").upper()
    if freq == "MONTHLY":
        return float(g.recurrence_amount)
    if freq == "QUARTERLY":
        return float(g.recurrence_amount) / 3.0
    if freq in ("ANNUAL", "YEARLY"):
        return float(g.recurrence_amount) / 12.0
    return float(g.recurrence_amount)


def _recurring_is_active(g: SimulationGoal, month_first: datetime.date) -> bool:
    """True if *month_first* is inside [recurrence_start, recurrence_end] (inclusive)."""
    if g.recurrence_start is None:
        return True
    rs, re = g.recurrence_start, g.recurrence_end
    if month_first < rs.replace(day=1):
        return False
    if re is not None and month_first > re.replace(day=1):
        return False
    return True


def _sort_goals_for_allocation(goals: list[SimulationGoal]) -> list[SimulationGoal]:
    """Non-GROWTH first (by allocation_priority), then GROWTH (by allocation_priority)."""

    def key(g: SimulationGoal) -> tuple[int, int]:
        is_growth = 1 if g.goal_class.upper() == GC_GROWTH else 0
        return (is_growth, g.allocation_priority)

    return sorted(goals, key=key)


def _inflation_target_at_month(
    raw_target: float,
    annual_inflation_pct: float,
    month_index_0: int,
) -> float:
    """Target in future rupees at simulation month *month_index_0* (0 = first month)."""
    years = (month_index_0 + 1) / 12.0
    return raw_target * (1.0 + annual_inflation_pct / 100.0) ** years


def _pmt_needed(
    gap: float,
    months_left: int,
    annual_return_pct: float,
) -> float:
    """Monthly payment to close *gap* in *months_left* months at *annual_return_pct*."""
    if gap <= 0:
        return 0.0
    n = max(1, months_left)
    r = _monthly_r(annual_return_pct)
    if r > 0:
        return gap * r / ((1.0 + r) ** n - 1.0)
    return gap / n


def compute_target_at_month(
    goal: SimulationGoal,
    month_number: int,
    monthly_required: float,
) -> float | None:
    """Expected corpus at 1-based *month_number* using compound growth + level contributions.

    For POINT_IN_TIME only; returns None for other classes.
    """
    if goal.goal_class.upper() != GC_POINT:
        return None
    if month_number < 1:
        return None
    m = month_number
    r = _monthly_r(goal.expected_return_rate)
    pv = float(goal.starting_balance or 0.0)
    pmt = monthly_required
    if r > 0:
        return pv * (1.0 + r) ** m + pmt * ((1.0 + r) ** m - 1.0) / r
    return pv + pmt * m


def allocate_surplus(
    goals: list[SimulationGoal],
    surplus: float,
    today: datetime.date | None = None,
    *,
    general_inflation_rate: float = 6.0,
) -> dict[str, float]:
    """Single-pass priority waterfall — no month loop. For overview / quick UI.

    When a POINT_IN_TIME goal has ``inflation_rate is None``, *general_inflation_rate*
    is used (same rule as :func:`simulate`).
    """
    if not goals:
        return {}
    td = today or datetime.date.today()
    month_start = td.replace(day=1)
    remaining = max(0.0, float(surplus))
    out: dict[str, float] = {g.name: 0.0 for g in goals}
    ordered = _sort_goals_for_allocation(goals)

    for g in ordered:
        gc = g.goal_class.upper()
        if gc == GC_GROWTH:
            continue
        if gc == GC_RECURRING:
            if not _recurring_is_active(g, month_start):
                continue
            need = _recurring_monthly_need(g)
            take = min(need, remaining)
            out[g.name] += take
            remaining -= take
            continue
        if gc == GC_POINT:
            if g.target_amount is None or g.target_date is None:
                continue
            n = months_between(month_start, g.target_date)
            if n <= 0:
                continue
            raw = float(g.target_amount)
            years = n / 12.0
            eff_infl = _effective_goal_inflation(g, general_inflation_rate)
            adj = raw * (1.0 + eff_infl / 100.0) ** years
            pv = float(g.starting_balance or 0.0)
            r = _monthly_r(g.expected_return_rate)
            fv_pv = pv * (1.0 + r) ** n if r > 0 else pv
            gap = max(0.0, adj - fv_pv)
            need = _pmt_needed(gap, n, g.expected_return_rate)
            take = min(need, remaining)
            out[g.name] += take
            remaining -= take

    growth_goals = [g for g in ordered if g.goal_class.upper() == GC_GROWTH]
    if growth_goals:
        share = remaining / len(growth_goals)
        for g in growth_goals:
            out[g.name] += share
        remaining = 0.0

    return out


def simulate(params: SimulationParams) -> SimulationResult:
    """Month-by-month projection; deterministic given params. No I/O."""
    warnings: list[str] = []
    as_of = params.as_of_date or datetime.date.today()
    start_month = as_of.replace(day=1)

    goals = params.goals
    if not goals:
        return SimulationResult(projections=[], warnings=["No goals in simulation."])

    for g in goals:
        if g.goal_class.upper() == GC_POINT and (
            g.target_amount is None or g.target_date is None
        ):
            warnings.append(
                f"POINT_IN_TIME goal {g.name!r} missing target_amount or target_date — skipped for PMT.",
            )

    # Mutable per-goal state
    n_goals = len(goals)
    current_value: list[float] = [float(g.starting_balance or 0.0) for g in goals]
    completed: list[bool] = [False] * n_goals
    completion_date: list[datetime.date | None] = [None] * n_goals
    total_allocated: list[float] = [0.0] * n_goals
    trajectories: list[list[MonthlySnapshot]] = [[] for _ in range(n_goals)]

    cascade_events: list[CascadeEvent] = []
    net_worth: list[MonthlyNetWorth] = []

    active_surplus = max(0.0, float(params.monthly_surplus))
    sum_unallocated = 0.0

    # Steady PMT from t=0 for run-rate chart (POINT_IN_TIME only)
    steady_pmt: list[float] = [0.0] * n_goals
    for i, g in enumerate(goals):
        if g.goal_class.upper() != GC_POINT or g.target_amount is None or g.target_date is None:
            continue
        n0 = months_between(start_month, g.target_date)
        if n0 <= 0:
            continue
        raw_target = float(g.target_amount)
        years = n0 / 12.0
        eff_infl = _effective_goal_inflation(g, params.general_inflation_rate)
        adj = raw_target * (1.0 + eff_infl / 100.0) ** years
        pv = float(g.starting_balance or 0.0)
        r = _monthly_r(g.expected_return_rate)
        fv_pv = pv * (1.0 + r) ** n0 if r > 0 else pv
        gap = max(0.0, adj - fv_pv)
        steady_pmt[i] = _pmt_needed(gap, n0, g.expected_return_rate)

    by_idx = list(range(n_goals))

    for m in range(params.simulation_months):
        current_month = add_months(start_month, m)

        # Salary growth at start of year 2+ (after every 12 completed months)
        if m > 0 and m % 12 == 0 and params.salary_growth_rate > 0:
            active_surplus *= 1.0 + params.salary_growth_rate / 100.0

        # One-time adjustments this calendar month
        extra = 0.0
        for ev in params.one_time_inflows:
            if ev.date.year == current_month.year and ev.date.month == current_month.month:
                extra += ev.amount
        for ev in params.one_time_outflows:
            if ev.date.year == current_month.year and ev.date.month == current_month.month:
                extra -= ev.amount

        remaining_surplus = max(0.0, active_surplus + extra)

        ordered_indices = sorted(
            by_idx,
            key=lambda idx: (
                1 if goals[idx].goal_class.upper() == GC_GROWTH else 0,
                goals[idx].allocation_priority,
            ),
        )

        alloc_this_month: dict[int, float] = {i: 0.0 for i in range(n_goals)}

        # POINT_IN_TIME + RECURRING first (by priority among non-GROWTH)
        for i in ordered_indices:
            if completed[i]:
                continue
            g = goals[i]
            gc = g.goal_class.upper()
            if gc == GC_GROWTH:
                continue

            if gc == GC_RECURRING:
                if not _recurring_is_active(g, current_month):
                    continue
                need = _recurring_monthly_need(g)
                take = min(need, remaining_surplus)
                alloc_this_month[i] = take
                remaining_surplus -= take
                continue

            if gc == GC_POINT:
                if g.target_amount is None or g.target_date is None:
                    continue
                raw_target = float(g.target_amount)
                n_left = months_between(current_month, g.target_date)
                if n_left <= 0:
                    continue

                eff_infl = _effective_goal_inflation(g, params.general_inflation_rate)
                infl_t = _inflation_target_at_month(raw_target, eff_infl, m)
                r = _monthly_r(g.expected_return_rate)
                fv_pv = current_value[i] * (1.0 + r) ** n_left if r > 0 else current_value[i]
                gap = max(0.0, infl_t - fv_pv)
                need = _pmt_needed(gap, n_left, g.expected_return_rate)
                take = min(need, remaining_surplus)
                alloc_this_month[i] = take
                remaining_surplus -= take

        # GROWTH goals share whatever is left
        growth_idxs = [
            i
            for i in ordered_indices
            if goals[i].goal_class.upper() == GC_GROWTH and not completed[i]
        ]
        if growth_idxs and remaining_surplus > 0:
            share = remaining_surplus / len(growth_idxs)
            for i in growth_idxs:
                alloc_this_month[i] += share
            remaining_surplus = 0.0

        sum_unallocated += max(0.0, remaining_surplus)

        # Apply returns then contributions; record snapshots
        total_v = 0.0
        total_c = 0.0
        total_r = 0.0

        for i in range(n_goals):
            g = goals[i]
            r_m = _monthly_r(g.expected_return_rate)
            ret = current_value[i] * r_m
            contrib = alloc_this_month[i]
            current_value[i] += ret + contrib
            total_allocated[i] += contrib

            tgt_snap: float | None = None
            if g.goal_class.upper() == GC_POINT and steady_pmt[i] > 0:
                tgt_snap = compute_target_at_month(g, m + 1, steady_pmt[i])

            trajectories[i].append(
                MonthlySnapshot(
                    month=current_month,
                    cumulative_value=round(current_value[i], 2),
                    monthly_contribution=round(contrib, 2),
                    monthly_return=round(ret, 2),
                    target_at_month=round(tgt_snap, 2) if tgt_snap is not None else None,
                )
            )
            total_v += current_value[i]
            total_c += contrib
            total_r += ret

            # POINT_IN_TIME completion — one cascade per newly completed goal
            if (
                not completed[i]
                and g.goal_class.upper() == GC_POINT
                and g.target_amount is not None
            ):
                eff_infl = _effective_goal_inflation(g, params.general_inflation_rate)
                infl_t = _inflation_target_at_month(float(g.target_amount), eff_infl, m)
                if current_value[i] >= infl_t - 1e-6:
                    completed[i] = True
                    completion_date[i] = current_month
                    freed = alloc_this_month[i]
                    beneficiaries = [
                        goals[j].name
                        for j in ordered_indices
                        if j != i
                        and not completed[j]
                        and goals[j].goal_class.upper() != GC_GROWTH
                    ]
                    cascade_events.append(
                        CascadeEvent(
                            month=current_month,
                            completed_goal=g.name,
                            freed_surplus=freed,
                            beneficiary_goals=beneficiaries[:10],
                        )
                    )

        net_worth.append(
            MonthlyNetWorth(
                month=current_month,
                total_value=round(total_v, 2),
                total_contributions=round(total_c, 2),
                total_returns=round(total_r, 2),
            )
        )

    # Build projections
    projections: list[GoalProjection] = []
    surplus_map: dict[str, float] = {}
    sim_months = max(1, params.simulation_months)

    for i, g in enumerate(goals):
        avg_alloc = total_allocated[i] / sim_months
        surplus_map[g.name] = round(avg_alloc, 2)
        final_amt = current_value[i]

        st: GoalSimStatus = "ON_TRACK"
        shortfall = 0.0

        if g.goal_class.upper() == GC_POINT and g.target_amount is not None:
            eff_infl = _effective_goal_inflation(g, params.general_inflation_rate)
            final_target = _inflation_target_at_month(
                float(g.target_amount),
                eff_infl,
                params.simulation_months - 1,
            )
            if completed[i]:
                st = "ACHIEVED"
            elif final_amt >= final_target * 0.99:
                st = "ON_TRACK"
            elif final_amt >= final_target * 0.8:
                st = "AT_RISK"
            elif final_amt >= final_target * 0.2:
                st = "BEHIND"
            else:
                st = "IMPOSSIBLE"
            shortfall = max(0.0, final_target - final_amt)
        elif g.goal_class.upper() == GC_RECURRING:
            st = "ON_TRACK"
            shortfall = 0.0
        else:
            st = "ON_TRACK"
            shortfall = 0.0

        projections.append(
            GoalProjection(
                goal_id=g.id,
                goal_name=g.name,
                monthly_allocation=round(avg_alloc, 2),
                projected_completion_date=completion_date[i],
                status=st,
                projected_final_amount=round(final_amt, 2),
                shortfall=round(shortfall, 2),
                monthly_trajectory=trajectories[i],
            )
        )

    projections.sort(key=lambda p: (p.status != "ACHIEVED", p.goal_name))

    total_alloc = sum(surplus_map.values())
    avg_unallocated = sum_unallocated / sim_months

    return SimulationResult(
        projections=projections,
        surplus_allocation=surplus_map,
        total_surplus_allocated=round(total_alloc, 2),
        unallocated_surplus=round(avg_unallocated, 2),
        cascade_events=cascade_events,
        net_worth_projection=net_worth,
        warnings=warnings,
    )


def _diff_params(base: SimulationParams, variant: SimulationParams) -> dict[str, Any]:
    """Shallow diff for scenario comparison UX."""
    d: dict[str, Any] = {}
    if base.monthly_surplus != variant.monthly_surplus:
        d["monthly_surplus"] = {"from": base.monthly_surplus, "to": variant.monthly_surplus}
    if base.salary_growth_rate != variant.salary_growth_rate:
        d["salary_growth_rate"] = {"from": base.salary_growth_rate, "to": variant.salary_growth_rate}
    if base.simulation_months != variant.simulation_months:
        d["simulation_months"] = {"from": base.simulation_months, "to": variant.simulation_months}
    if len(base.goals) != len(variant.goals):
        d["goals_count"] = {"from": len(base.goals), "to": len(variant.goals)}
    return d


def compare_scenarios(
    base: SimulationParams,
    variants: list[SimulationParams],
) -> list[ScenarioComparison]:
    """Run :func:`simulate` on *base* and each variant; compute per-goal deltas."""
    base_res = simulate(base)
    base_by_name = {p.goal_name: p for p in base_res.projections}
    out: list[ScenarioComparison] = []

    for idx, var in enumerate(variants):
        name = f"variant_{idx + 1}"
        res = simulate(var)
        deltas: list[GoalDelta] = []
        var_by_name = {p.goal_name: p for p in res.projections}

        for gn, bp in base_by_name.items():
            vp = var_by_name.get(gn)
            if vp is None:
                continue
            bcd, vcd = bp.projected_completion_date, vp.projected_completion_date
            ms: int | None = None
            if bcd and vcd:
                # Negative = variant completes earlier (better).
                ms = -int((bcd - vcd).days / 30.44)
            elif vcd and not bcd:
                ms = -60
            elif bcd and not vcd:
                ms = 60

            deltas.append(
                GoalDelta(
                    goal_name=gn,
                    base_completion=bcd,
                    variant_completion=vcd,
                    base_status=bp.status,
                    variant_status=vp.status,
                    months_shifted=ms,
                )
            )

        out.append(
            ScenarioComparison(
                scenario_name=name,
                changes_from_base=_diff_params(base, var),
                result=res,
                deltas=deltas,
            )
        )

    return out


