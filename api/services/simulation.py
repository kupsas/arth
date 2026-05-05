"""
Goals architecture Sub-Plan G — pure-function simulation engine.

No database access inside :func:`simulate`. Callers map DB rows to :class:`SimulationGoal`
and pass :class:`SimulationParams`; results are JSON-serializable Pydantic models.

:func:`simulate` runs :func:`_simulate_inner` one or more times with cascade-aware PMT
ceilings so lower-priority POINT_IN_TIME goals that would finish early do not starve
higher-priority goals. :func:`allocate_surplus` remains a single-pass snapshot.

See ``docs/personal-data/goals-architecture-master-plan.md`` § Sub-Plan G.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from api.services.goal_decomposer import add_months, months_between
from api.services.inflation_service import GOAL_INFLATION_MAP, INFLATION_DEFAULTS

logger = logging.getLogger(__name__)

# ── Goal class constants (aligned with pipeline / API) ───────────────────────
GC_POINT = "POINT_IN_TIME"
GC_RECURRING = "RECURRING_CASH_FLOW"

# After this many months from the recurring window anchor, the nominal need escalates
# yearly by the goal’s inflation rate (matches decomposer short-horizon idea).
RECURRING_INFLATION_GRACE_MONTHS = 24

# Recurring goals with these subtypes are funded like non-negotiable bills *before*
# any POINT_IN_TIME / discretionary recurring allocation. All other RECURRING goals
# compete with PIT by allocation_priority (they are not “pay first” obligations).
MANDATORY_RECURRING_SUBTYPES: frozenset[str] = frozenset(
    {"LOAN_PAYOFF", "EMERGENCY_FUND", "CHILD_EDUCATION"},
)

# Any positive monthly flow below this is treated as zero; freed rupees follow overflow rules
# (same sink as post-minimum surplus), else count as unallocated. Matches dashboard validation.
MIN_MONTHLY_GOAL_CONTRIBUTION_INR = 5000.0

# Cascade-aware PMT refinement: re-simulate with caps until stable (see :func:`simulate`).
MAX_REFINEMENT_PASSES = 5
_CEILING_REL_TOL = 0.01  # 1% relative change → converged

# Observation-first clash caps (:func:`_compute_pmt_ceilings`). A closed-form “theoretical
# PMT” from FV math can be ~73k while the priority waterfall only ever allocates ~29k to
# FIRE — those are not comparable; caps must come from **observed** clash-month flows.
_CLASH_EARLY_MONTHS = 30  # first N months while predecessor is still open (early-days anchor)
# Lower = more aggressive pull on FIRE during clash (more surplus freed for higher-priority PIT).
_CLASH_CAP_EARLY_SHRINK = 0.50
_CLASH_CAP_FULL_SHRINK = 0.60

# Set ``ARTH_SIMULATION_DEBUG=1`` or ``arth_simulation_debug=1`` in the environment (e.g. ``.env``)
# to log refinement passes, ceilings, and PIT summaries at DEBUG level (see :func:`simulate`).
# Both spellings are accepted — ``.env`` files often use lowercase keys; Unix env vars are case-sensitive.
_SIM_DEBUG_KEYS = ("ARTH_SIMULATION_DEBUG", "arth_simulation_debug")


def _simulation_debug_enabled() -> bool:
    for key in _SIM_DEBUG_KEYS:
        v = os.environ.get(key, "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    return False


def _recurring_is_mandatory_bill(goal: SimulationGoal) -> bool:
    """True if this recurring goal must be funded before PIT / discretionary recurring."""
    if goal.goal_class.upper() != GC_RECURRING:
        return False
    st = (goal.goal_subtype or "").strip().upper()
    return st in MANDATORY_RECURRING_SUBTYPES


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
        description="POINT_IN_TIME | RECURRING_CASH_FLOW",
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
            "Annual % — inflates POINT_IN_TIME targets and recurring needs (after grace). "
            "None uses SimulationParams.general_inflation_rate."
        ),
    )
    inflation_category: str | None = Field(
        default=None,
        description="InflationRate category key when hydrated from DB (REAL_ESTATE, …) — UI only.",
    )
    inflation_method: str | None = Field(
        default=None,
        description="How inflation_rate was resolved — UI only (user_override, category_default, …).",
    )
    inflation_label: str | None = Field(
        default=None,
        description="Short human label for the inflation bucket — UI only.",
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
        description=(
            "Headline CPI annual % when SimulationGoal.inflation_rate is None — typically "
            "the same EMA as production (IMF YoY); 6 is only a JSON default."
        ),
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
    monthly_need: float | None = Field(
        None,
        description="Engine amortized need this month (PIT dynamic PMT, recurring monthly need).",
    )


class GoalProjection(BaseModel):
    goal_id: int | None
    goal_name: str
    monthly_allocation: float = Field(
        ...,
        description="Average monthly INR allocated to this goal over the horizon",
    )
    projected_completion_date: datetime.date | None = None
    # PIT: corpus at the deadline month (trajectory) / inflation-adjusted target there × 100 (uncapped)
    projected_completion_pct: float | None = None
    corpus_at_deadline: float | None = None
    inflation_adjusted_target_at_deadline: float | None = None
    shortfall_at_deadline: float | None = None
    # Recurring: forward-projected window — periods that meet need / total billable periods × 100
    periods_met_pct: float | None = None
    worst_period_deficit: float | None = Field(
        None,
        description="RECURRING: largest (need − contribution) within any billing period chunk.",
    )
    projected_final_amount: float
    shortfall: float = Field(
        ...,
        description=(
            "PIT: shortfall_at_deadline when available; else legacy end-of-horizon gap. "
            "RECURRING: max(0, total_needed - total_contributed) over the trajectory."
        ),
    )
    monthly_trajectory: list[MonthlySnapshot] = Field(default_factory=list)
    periods_total: int | None = Field(
        None,
        description="RECURRING: billing periods with positive need (chunked by recurrence frequency).",
    )
    periods_funded: int | None = Field(
        None,
        description="RECURRING: periods where contribution sum >= 95% of need sum.",
    )
    funding_rate: float | None = Field(
        None,
        description="RECURRING: periods_funded / periods_total when periods_total > 0.",
    )
    total_contributed: float | None = Field(
        None,
        description="RECURRING: sum of monthly_contribution over the trajectory.",
    )
    total_needed: float | None = Field(
        None,
        description="RECURRING: sum of monthly_need over the trajectory.",
    )


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
    monthly_surplus_pool: float = Field(
        0.0,
        description="Investable surplus for this month (before allocation); equals goals + unallocated.",
    )
    unallocated_surplus: float = Field(
        0.0,
        description="Surplus not allocated to any goal after rules (spill, no sink, etc.).",
    )


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
    # Headline % for the goal: PIT → projected_completion_pct, recurring → periods_met_pct
    base_progress_pct: float | None = None
    variant_progress_pct: float | None = None
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
    """Annual inflation % for targets and recurring escalation.

    Explicit ``inflation_rate`` wins. When None (sandbox JSON), resolve from
    ``goal_subtype`` + :data:`GOAL_INFLATION_MAP` — same rules as
    :func:`api.services.inflation_service.resolve_goal_inflation`: ``CPI_GENERAL``
    uses *general_inflation_pct* (headline EMA in production), other categories
    use :data:`INFLATION_DEFAULTS`, ``LOAN_PAYOFF`` → 0.
    """
    if goal.inflation_rate is not None:
        return float(goal.inflation_rate)
    st = (goal.goal_subtype or "CUSTOM").strip().upper()
    mapped = GOAL_INFLATION_MAP.get(st, "CPI_GENERAL")
    if mapped is None:
        return 0.0
    if mapped == "CPI_GENERAL":
        return float(general_inflation_pct)
    return float(INFLATION_DEFAULTS.get(mapped, INFLATION_DEFAULTS["CPI_GENERAL"]))


def _monthly_r(annual_pct: float) -> float:
    return annual_pct / 12.0 / 100.0


def _recurring_monthly_need(g: SimulationGoal) -> float:
    """Convert recurrence_amount to an average monthly INR need while active (base, year 0)."""
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


def _recurring_escalation_factor(months_since_anchor: int, annual_inflation_pct: float) -> float:
    """1.0 for the first RECURRING_INFLATION_GRACE_MONTHS; then compound per year after."""
    if months_since_anchor < RECURRING_INFLATION_GRACE_MONTHS:
        return 1.0
    rel = months_since_anchor - RECURRING_INFLATION_GRACE_MONTHS
    exponent = (rel // 12) + 1
    return (1.0 + annual_inflation_pct / 100.0) ** exponent


def _recurring_monthly_need_for_month(
    g: SimulationGoal,
    current_month_first: datetime.date,
    simulation_start_month: datetime.date,
    general_inflation_pct: float,
) -> float:
    """Base monthly recurrence, escalated after the grace window using goal inflation."""
    base = _recurring_monthly_need(g)
    if base <= 0:
        return 0.0
    anchor = (
        g.recurrence_start.replace(day=1)
        if g.recurrence_start is not None
        else simulation_start_month
    )
    if current_month_first < anchor:
        return 0.0
    ms = months_between(anchor, current_month_first)
    eff = _effective_goal_inflation(g, general_inflation_pct)
    return base * _recurring_escalation_factor(ms, eff)


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


def _recurrence_period_months(freq: str | None) -> int:
    """Months per billing period for chunking recurring funding stats."""
    f = (freq or "MONTHLY").strip().upper()
    if f == "QUARTERLY":
        return 3
    if f in ("ANNUAL", "YEARLY"):
        return 12
    return 1


def _compute_recurring_funding_stats(
    goal: SimulationGoal,
    trajectory: list[MonthlySnapshot],
) -> tuple[
    int | None,
    int | None,
    float | None,
    float | None,
    float | None,
    float | None,
]:
    """Recurring: period counts, funding_rate, flows, and worst in-period (need−contrib) gap."""
    if goal.goal_class.upper() != GC_RECURRING:
        return None, None, None, None, None, None
    pm = _recurrence_period_months(goal.recurrence_frequency)
    total_contributed = round(sum(s.monthly_contribution for s in trajectory), 2)
    total_needed = round(sum(s.monthly_need or 0.0 for s in trajectory), 2)

    # Chunk from the first month with positive amortized need through the last — avoids
    # misaligned QUARTERLY/ANNUAL windows when recurrence_start is mid-simulation (or after
    # recurrence_end, trailing months have need 0 and must not dilute the last billing period).
    start_idx: int | None = None
    end_idx: int | None = None
    for j, s in enumerate(trajectory):
        if (s.monthly_need or 0.0) > 1e-6:
            start_idx = j
            break
    if start_idx is None:
        return 0, 0, None, total_contributed, total_needed, None
    end_idx = start_idx
    for j in range(len(trajectory) - 1, start_idx - 1, -1):
        if (trajectory[j].monthly_need or 0.0) > 1e-6:
            end_idx = j
            break
    segment = trajectory[start_idx : end_idx + 1]

    periods_total = 0
    periods_funded = 0
    worst_gap = 0.0
    i = 0
    while i < len(segment):
        chunk = segment[i : i + pm]
        i += pm
        need_sum = sum(s.monthly_need or 0.0 for s in chunk)
        contrib_sum = sum(s.monthly_contribution for s in chunk)
        if need_sum <= 1e-6:
            continue
        gap = max(0.0, need_sum - contrib_sum)
        if gap > worst_gap:
            worst_gap = gap
        periods_total += 1
        if contrib_sum + 1e-6 >= need_sum * 0.95:
            periods_funded += 1

    if periods_total <= 0:
        return 0, 0, None, total_contributed, total_needed, None

    fr = round(periods_funded / periods_total, 4)
    worst = round(worst_gap, 2) if worst_gap > 1e-6 else None
    return periods_total, periods_funded, fr, total_contributed, total_needed, worst


def _pit_deadline_financials(
    g: SimulationGoal,
    trajectory: list[MonthlySnapshot],
    start_month: datetime.date,
    sim_months: int,
    general_inflation_pct: float,
) -> tuple[float, float, float, float, float]:
    """PIT: at target month and at end of horizon (last tuple value = legacy ``shortfall``)."""
    if (
        g.goal_class.upper() != GC_POINT
        or g.target_amount is None
        or g.target_date is None
    ):
        return 0.0, 0.0, 0.0, 0.0, 0.0
    td0 = g.target_date.replace(day=1)
    m_deadline = months_between(start_month, td0)
    eff_infl = _effective_goal_inflation(g, general_inflation_pct)
    raw = float(g.target_amount)
    infl_t = _inflation_target_at_month(raw, eff_infl, m_deadline)
    if not trajectory:
        sfd = max(0.0, infl_t)
        pct = 0.0
        return 0.0, round(infl_t, 2), round(sfd, 2), round(pct, 2), round(sfd, 2)

    idx = min(m_deadline, len(trajectory) - 1)
    idx = max(0, idx)
    corpus = float(trajectory[idx].cumulative_value)
    if infl_t <= 1e-9:
        pct = 0.0
    else:
        pct = (corpus / infl_t) * 100.0
    sfd = max(0.0, infl_t - corpus)
    last_m = sim_months - 1
    if last_m < 0:
        last_m = 0
    end_corpus = float(trajectory[-1].cumulative_value)
    end_target = _inflation_target_at_month(raw, eff_infl, last_m)
    end_gap = max(0.0, end_target - end_corpus)
    return (
        round(corpus, 2),
        round(infl_t, 2),
        round(sfd, 2),
        round(pct, 2),
        round(end_gap, 2),
    )


def _sort_goals_for_allocation(goals: list[SimulationGoal]) -> list[SimulationGoal]:
    """Lower ``allocation_priority`` first (1 = highest priority)."""

    return sorted(goals, key=lambda g: g.allocation_priority)


def _pick_overflow_goal_index(
    goals: list[SimulationGoal],
    ordered_indices: list[int],
    candidate_indices: list[int],
) -> int | None:
    """Lowest ``allocation_priority`` wins; ties break by earlier position in *ordered_indices*."""
    if not candidate_indices:
        return None
    pos = {idx: p for p, idx in enumerate(ordered_indices)}
    return min(
        candidate_indices,
        key=lambda i: (goals[i].allocation_priority, pos.get(i, 10**9)),
    )


def _overflow_candidate_indices_simulate(
    goals: list[SimulationGoal],
    ordered_indices: list[int],
    completed: list[bool],
    current_month: datetime.date,
) -> list[int]:
    """Goals that may absorb post-minimum surplus: open POINT_IN_TIME only — not RECURRING."""
    out: list[int] = []
    for i in ordered_indices:
        if completed[i]:
            continue
        g = goals[i]
        gc = g.goal_class.upper()
        if gc == GC_POINT:
            if g.target_amount is None or g.target_date is None:
                continue
            if months_between(current_month, g.target_date) <= 0:
                continue
            out.append(i)
    return out


def _inflation_target_at_month(
    raw_target: float,
    annual_inflation_pct: float,
    month_index_0: int,
) -> float:
    """Target in future rupees at simulation month *month_index_0* (0 = first month)."""
    years = (month_index_0 + 1) / 12.0
    return raw_target * (1.0 + annual_inflation_pct / 100.0) ** years


def _pit_dynamic_need(
    g: SimulationGoal,
    month_index_0: int,
    current_month: datetime.date,
    current_value: float,
    general_inflation_pct: float,
    salary_growth_pct: float,
) -> float:
    """Amortized monthly need for a POINT_IN_TIME goal this month (0 if not applicable)."""
    if g.goal_class.upper() != GC_POINT or g.target_amount is None or g.target_date is None:
        return 0.0
    n_left = months_between(current_month, g.target_date)
    if n_left <= 0:
        return 0.0
    raw_target = float(g.target_amount)
    eff_infl = _effective_goal_inflation(g, general_inflation_pct)
    infl_t = _inflation_target_at_month(raw_target, eff_infl, month_index_0)
    r = _monthly_r(g.expected_return_rate)
    fv_pv = current_value * (1.0 + r) ** n_left if r > 0 else current_value
    gap = max(0.0, infl_t - fv_pv)
    return _pmt_needed(
        gap,
        n_left,
        g.expected_return_rate,
        annual_salary_growth_pct=salary_growth_pct,
    )


def _redistribute_excess_to_shortfalls(
    goals: list[SimulationGoal],
    alloc_this_month: dict[int, float],
    need_by_idx: dict[int, float],
    completed: list[bool],
) -> None:
    """Move allocation from goals above their *need* to goals below *need*.

    Takes from goals with excess in ascending ``allocation_priority`` order (lower
    number = first to give up, matching who usually absorbed overflow). Fills
    shortfalls in ascending POINT_IN_TIME ``target_date`` order, then recurring.
    """

    def _sf_key(item: tuple[int, float]) -> tuple[int, int]:
        i, _ = item
        gg = goals[i]
        if gg.goal_class.upper() == GC_POINT and gg.target_date is not None:
            return (0, gg.target_date.toordinal())
        if gg.recurrence_start is not None:
            return (1, gg.recurrence_start.toordinal())
        return (2, i)

    excess: list[tuple[int, float]] = []
    shortfall: list[tuple[int, float]] = []
    for i, g in enumerate(goals):
        if completed[i]:
            continue
        need = float(need_by_idx.get(i, 0.0))
        a = float(alloc_this_month.get(i, 0.0))
        if a > need + 1e-6:
            excess.append((i, a - need))
        elif need > a + 1e-6:
            shortfall.append((i, need - a))

    pool = sum(e for _, e in excess)
    if pool <= 1e-9 or not shortfall:
        return

    shortfall.sort(key=_sf_key)
    remaining_pool = pool
    for j, sf in shortfall:
        add = min(sf, remaining_pool)
        if add <= 1e-9:
            continue
        alloc_this_month[j] = alloc_this_month.get(j, 0.0) + add
        remaining_pool -= add
    total_moved = pool - remaining_pool
    if total_moved <= 1e-9:
        return
    remaining = total_moved
    excess_sorted = sorted(excess, key=lambda t: (goals[t[0]].allocation_priority, t[0]))
    for k, ex_amt in excess_sorted:
        sub = min(ex_amt, remaining)
        alloc_this_month[k] = alloc_this_month.get(k, 0.0) - sub
        remaining -= sub
        if remaining <= 1e-9:
            break


def _apply_minimum_monthly_contribution_floor(
    goals: list[SimulationGoal],
    alloc_this_month: dict[int, float],
    ordered_indices: list[int],
    completed: list[bool],
    current_month: datetime.date,
) -> float:
    """Zero allocations in ``(0, MIN)``; add freed cash to the overflow sink or return spill.

    Returns rupees **not** placed on any goal (no open PIT sink), to add to unallocated.
    """
    min_inr = MIN_MONTHLY_GOAL_CONTRIBUTION_INR
    freed = 0.0
    for i in range(len(goals)):
        if completed[i]:
            continue
        a = float(alloc_this_month.get(i, 0.0))
        if 0.0 < a < min_inr:
            freed += a
            alloc_this_month[i] = 0.0
    if freed <= 1e-9:
        return 0.0
    cand = _overflow_candidate_indices_simulate(
        goals, ordered_indices, completed, current_month
    )
    win = _pick_overflow_goal_index(goals, ordered_indices, cand)
    if win is not None:
        alloc_this_month[win] = alloc_this_month.get(win, 0.0) + freed
        return 0.0
    return freed


def _allocate_surplus_apply_minimum_floor(
    ordered: list[SimulationGoal],
    out: dict[str, float],
    month_start: datetime.date,
) -> None:
    """Same rule as :func:`_apply_minimum_monthly_contribution_floor` for name-keyed snapshot."""
    min_inr = MIN_MONTHLY_GOAL_CONTRIBUTION_INR
    freed = 0.0
    for g in ordered:
        a = float(out.get(g.name, 0.0))
        if 0.0 < a < min_inr:
            freed += a
            out[g.name] = 0.0
    if freed <= 1e-9:
        return
    cand_j: list[int] = []
    for j, g in enumerate(ordered):
        gc = g.goal_class.upper()
        if gc == GC_POINT:
            if g.target_amount is None or g.target_date is None:
                continue
            if months_between(month_start, g.target_date) <= 0:
                continue
            cand_j.append(j)
    if cand_j:
        wj = min(cand_j, key=lambda j: (ordered[j].allocation_priority, j))
        out[ordered[wj].name] = out.get(ordered[wj].name, 0.0) + freed


def _pmt_needed(
    gap: float,
    months_left: int,
    annual_return_pct: float,
    *,
    annual_salary_growth_pct: float = 0.0,
) -> float:
    """Monthly payment to close *gap* in *months_left* months at *annual_return_pct*.

    When *annual_salary_growth_pct* is 0, uses the **level** annuity (constant
    contributions every month). When positive, uses a **growing** annuity: the
    first payment is lower because later payments are assumed to grow at the
    same rate as monthly surplus (salary growth), matching :func:`simulate`.

    Level-annuity formula (payment *P* at each month, *r* = monthly return):

      P = gap × r / ((1+r)^n − 1)

    Growing-annuity (first payment *P₀*, contributions grow at monthly rate *g*):

      P₀ = gap × (r − g) / ((1+r)^n − (1+g)^n)

    When *r* ≈ *g*, uses *P₀ = gap / (n × (1+r)^(n−1))* (or *gap/n* if *r* = 0).
    """
    if gap <= 0:
        return 0.0
    n = max(1, months_left)
    r = _monthly_r(annual_return_pct)
    if annual_salary_growth_pct <= 0.0:
        if r > 0:
            return gap * r / ((1.0 + r) ** n - 1.0)
        return gap / n

    g = _monthly_r(annual_salary_growth_pct)
    if abs(r - g) < 1e-14:
        if r > 0:
            return gap / (n * (1.0 + r) ** (n - 1))
        return gap / n

    denom = (1.0 + r) ** n - (1.0 + g) ** n
    if abs(denom) < 1e-18:
        if r > 0:
            return gap / (n * (1.0 + r) ** (n - 1))
        return gap / n
    return gap * (r - g) / denom


def compute_target_at_month_with_growing_contributions(
    goal: SimulationGoal,
    month_number: int,
    pmt0: float,
    annual_salary_growth_pct: float,
) -> float | None:
    """POINT_IN_TIME: expected corpus at month *month_number* with first payment *pmt0*
    and contributions growing at the monthly equivalent of *annual_salary_growth_pct*.

    Uses the same FV identity as :func:`_pmt_needed` for the contribution leg:
    FV_contrib = P₀ × ((1+r)^m − (1+g)^m) / (r − g). When *r* ≈ *g*, uses
    P₀ × m × (1+r)^(m−1). Aligns the run-rate glide line with growing-PMT mode.
    """
    if goal.goal_class.upper() != GC_POINT:
        return None
    if month_number < 1:
        return None
    m = month_number
    r = _monthly_r(goal.expected_return_rate)
    pv = float(goal.starting_balance or 0.0)
    if annual_salary_growth_pct <= 0.0:
        return compute_target_at_month(goal, month_number, pmt0)

    g = _monthly_r(annual_salary_growth_pct)
    if abs(r - g) < 1e-14:
        fv_contrib = pmt0 * m * (1.0 + r) ** (m - 1) if m > 0 else 0.0
    else:
        denom = r - g
        if abs(denom) < 1e-18:
            fv_contrib = pmt0 * m * (1.0 + r) ** (m - 1) if m > 0 else 0.0
        else:
            fv_contrib = pmt0 * ((1.0 + r) ** m - (1.0 + g) ** m) / denom

    if r > 0:
        return pv * (1.0 + r) ** m + fv_contrib
    return pv + fv_contrib


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
    salary_growth_rate: float = 0.0,
) -> dict[str, float]:
    """Single-pass priority waterfall — no month loop. For overview / quick UI.

    **Pass 1 — mandatory recurring only** (``goal_subtype`` in loan / emergency /
    child education): each active goal takes min(monthly need, remaining surplus),
    in ``allocation_priority`` order.

    **Pass 2 — PIT + discretionary recurring**: same min(need, remaining) rule.
    Discretionary recurring does *not* jump ahead of PIT.

    **Pass 3 — overflow** to one sink: lowest ``allocation_priority`` among open PIT
    goals. RECURRING goals never absorb overflow (capped at their need).

    When a POINT_IN_TIME goal has ``inflation_rate is None``, *general_inflation_rate*
    is used (same rule as :func:`simulate`). When *salary_growth_rate* is positive,
    PMT uses the growing-annuity formula (same as :func:`simulate`).
    """
    if not goals:
        return {}
    goals = list(goals)
    td = today or datetime.date.today()
    month_start = td.replace(day=1)
    remaining = max(0.0, float(surplus))
    out: dict[str, float] = {g.name: 0.0 for g in goals}
    ordered = _sort_goals_for_allocation(goals)

    # Pass 1: mandatory recurring “bills” only (subtype-gated).
    for g in ordered:
        gc = g.goal_class.upper()
        if gc != GC_RECURRING:
            continue
        if not _recurring_is_mandatory_bill(g):
            continue
        if not _recurring_is_active(g, month_start):
            continue
        need = _recurring_monthly_need_for_month(
            g,
            month_start,
            month_start,
            general_inflation_rate,
        )
        take = min(need, remaining)
        out[g.name] += take
        remaining -= take

    # Pass 2: PIT + discretionary recurring (compete by allocation_priority).
    for g in ordered:
        gc = g.goal_class.upper()
        if gc == GC_RECURRING:
            if _recurring_is_mandatory_bill(g):
                continue
            if not _recurring_is_active(g, month_start):
                continue
            need = _recurring_monthly_need_for_month(
                g,
                month_start,
                month_start,
                general_inflation_rate,
            )
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
            need = _pmt_needed(
                gap,
                n,
                g.expected_return_rate,
                annual_salary_growth_pct=salary_growth_rate,
            )
            take = min(need, remaining)
            out[g.name] += take
            remaining -= take

    # Remaining surplus → single lowest allocation_priority among open PIT goals.
    # RECURRING goals never take more than their monthly need (EMI-style).
    if remaining > 0:
        cand_j: list[int] = []
        for j, g in enumerate(ordered):
            gc = g.goal_class.upper()
            if gc == GC_POINT:
                if g.target_amount is None or g.target_date is None:
                    continue
                if months_between(month_start, g.target_date) <= 0:
                    continue
                cand_j.append(j)
        if cand_j:
            wj = min(cand_j, key=lambda j: (ordered[j].allocation_priority, j))
            out[ordered[wj].name] += remaining
            remaining = 0.0

    _allocate_surplus_apply_minimum_floor(ordered, out, month_start)

    return out


def _ceilings_converged(
    prev: dict[int, float],
    curr: dict[int, float],
) -> bool:
    """True if relative change for every goal index is below tolerance."""
    if not prev and not curr:
        return True
    keys = set(prev) | set(curr)
    for k in keys:
        a, b = prev.get(k, 0.0), curr.get(k, 0.0)
        mx = max(abs(a), abs(b), 1.0)
        if abs(a - b) / mx > _CEILING_REL_TOL:
            return False
    return True


def _compute_pmt_ceilings(
    params: SimulationParams,
    result: SimulationResult,
) -> dict[int, float]:
    """Derive per-goal PMT caps for lower-priority PIT goals that finished early.

    **Observation-first:** the cap is derived from **actual** monthly contributions in the
    *clash window* (months while the immediate higher-priority PIT is still open), not from
    a standalone annuity formula that ignores House-vs-FIRE priority. We take the max
    contribution seen in the first :data:`_CLASH_EARLY_MONTHS` of that window and the max
    over the full window, apply shrink factors, and use ``min`` of the two so “early days”
    behaviour (e.g. ~29k) can dominate when later clash months spike.
    """
    goals = params.goals
    as_of = params.as_of_date or datetime.date.today()
    start_month = as_of.replace(day=1)
    by_name = {p.goal_name: p for p in result.projections}

    pit_indices = [
        i
        for i, g in enumerate(goals)
        if g.goal_class.upper() == GC_POINT and g.target_amount is not None and g.target_date is not None
    ]
    pit_indices.sort(key=lambda i: goals[i].allocation_priority)
    pit_order_pos = {idx: p for p, idx in enumerate(pit_indices)}

    ceilings: dict[int, float] = {}

    for i in pit_indices:
        g = goals[i]
        proj = by_name.get(g.name)
        if proj is None or proj.projected_completion_date is None:
            continue
        td = g.target_date
        assert td is not None
        cd = proj.projected_completion_date
        # Not "early" if completion month is on/after target month
        if cd.replace(day=1) >= td.replace(day=1):
            continue

        # Immediate predecessor in priority order (next-higher-priority PIT neighbour).
        ki = pit_order_pos[i]
        if ki == 0:
            continue
        blocker_i = pit_indices[ki - 1]
        blocker_proj = by_name.get(goals[blocker_i].name)
        if blocker_proj is None or blocker_proj.projected_completion_date is None:
            continue
        cascade_date = blocker_proj.projected_completion_date
        if cascade_date.replace(day=1) > cd.replace(day=1):
            continue

        n_total = months_between(start_month, td.replace(day=1))
        if n_total <= 0:
            continue

        M = months_between(start_month, cascade_date.replace(day=1))
        n1 = M + 1

        traj = proj.monthly_trajectory
        if not traj:
            continue

        # Clash months: indices 0 .. phase1_len-1 (while predecessor is still chasing its target).
        phase1_len = min(n1, len(traj))
        if phase1_len <= 0:
            continue

        contribs = [float(traj[m].monthly_contribution) for m in range(phase1_len)]
        max_full = max(contribs)
        early_len = min(phase1_len, _CLASH_EARLY_MONTHS)
        max_early = max(contribs[m] for m in range(early_len))

        cap_from_early = max_early * _CLASH_CAP_EARLY_SHRINK
        cap_from_full = max_full * _CLASH_CAP_FULL_SHRINK
        # Tightest binds: early-month behaviour can dominate even if FIRE spikes later pre-cascade.
        cap = min(cap_from_early, cap_from_full)
        cap = max(0.0, cap)

        if _simulation_debug_enabled():
            logger.debug(
                "refinement ceiling for goal_idx=%s goal_id=%s: clash months=%d max_early=%.2f "
                "max_full=%.2f → cap=%.2f (early_shrink=%.2f full_shrink=%.2f)",
                i,
                g.id,
                phase1_len,
                max_early,
                max_full,
                cap,
                _CLASH_CAP_EARLY_SHRINK,
                _CLASH_CAP_FULL_SHRINK,
            )

        ceilings[i] = cap

    return ceilings


def _log_simulation_debug_pit_snapshot(
    label: str,
    params: SimulationParams,
    result: SimulationResult,
) -> None:
    """One line per POINT_IN_TIME goal: status, completion vs target (refinement eligibility)."""
    goals = params.goals
    by_name = {p.goal_name: p for p in result.projections}
    parts: list[str] = []
    for i, g in enumerate(goals):
        if g.goal_class.upper() != GC_POINT or g.target_amount is None or g.target_date is None:
            continue
        pr = by_name.get(g.name)
        if pr is None:
            continue
        td = g.target_date
        cd = pr.projected_completion_date
        p_pct = pr.projected_completion_pct
        early = (
            cd is not None
            and td is not None
            and cd.replace(day=1) < td.replace(day=1)
        )
        parts.append(
            f"[{i}]goal_id={g.id!r} pri={g.allocation_priority} pct={p_pct} "
            f"done={cd} target={td} early={early}",
        )
    logger.debug("simulation %s: %s", label, "; ".join(parts) if parts else "(no PIT)")


def _projection_fully_successful(p: GoalProjection) -> bool:
    """True when PIT reached the target in-sim, or recurring funded every billable period."""
    if p.projected_completion_date is not None:
        return True
    if p.periods_met_pct is not None and p.periods_met_pct >= 100.0 - 1e-6:
        return True
    return False


def _headline_sim_progress_pct(p: GoalProjection) -> float | None:
    """The single % to show or compare: PIT completion % else recurring periods met %."""
    if p.projected_completion_pct is not None:
        return p.projected_completion_pct
    if p.periods_met_pct is not None:
        return p.periods_met_pct
    return None


def _simulate_inner(
    params: SimulationParams,
    pmt_ceilings: dict[int, float] | None,
) -> SimulationResult:
    """Single full month-by-month projection; optional PMT caps for POINT_IN_TIME goals."""
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

    ceilings = pmt_ceilings or {}

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

    # Steady PMT from t=0 for run-rate chart (POINT_IN_TIME only); respect refinement caps
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
        sp = _pmt_needed(
            gap,
            n0,
            g.expected_return_rate,
            annual_salary_growth_pct=params.salary_growth_rate,
        )
        cap = ceilings.get(i)
        steady_pmt[i] = min(sp, cap) if cap is not None else sp

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

        ordered_indices = sorted(by_idx, key=lambda idx: goals[idx].allocation_priority)

        alloc_this_month: dict[int, float] = {i: 0.0 for i in range(n_goals)}

        # Pass 1: mandatory recurring only (loan / emergency / child education subtypes).
        for i in ordered_indices:
            if completed[i]:
                continue
            g = goals[i]
            if g.goal_class.upper() != GC_RECURRING:
                continue
            if not _recurring_is_mandatory_bill(g):
                continue
            if not _recurring_is_active(g, current_month):
                continue
            need = _recurring_monthly_need_for_month(
                g,
                current_month,
                start_month,
                params.general_inflation_rate,
            )
            take = min(need, remaining_surplus)
            alloc_this_month[i] = take
            remaining_surplus -= take

        # Pass 2: POINT_IN_TIME + discretionary recurring (by allocation_priority).
        for i in ordered_indices:
            if completed[i]:
                continue
            g = goals[i]
            gc = g.goal_class.upper()

            if gc == GC_RECURRING:
                if _recurring_is_mandatory_bill(g):
                    continue
                if not _recurring_is_active(g, current_month):
                    continue
                need = _recurring_monthly_need_for_month(
                    g,
                    current_month,
                    start_month,
                    params.general_inflation_rate,
                )
                take = min(need, remaining_surplus)
                alloc_this_month[i] = take
                remaining_surplus -= take
                continue

            if gc == GC_POINT:
                if g.target_amount is None or g.target_date is None:
                    continue
                if months_between(current_month, g.target_date) <= 0:
                    continue

                need = _pit_dynamic_need(
                    g,
                    m,
                    current_month,
                    current_value[i],
                    params.general_inflation_rate,
                    params.salary_growth_rate,
                )
                cap = ceilings.get(i)
                if cap is not None:
                    need = min(need, cap)
                take = min(need, remaining_surplus)
                alloc_this_month[i] = take
                remaining_surplus -= take

        # Post-minimum surplus → one overflow bucket: lowest allocation_priority among
        # open POINT_IN_TIME goals. RECURRING does not absorb overflow.
        if remaining_surplus > 0:
            cand = _overflow_candidate_indices_simulate(
                goals, ordered_indices, completed, current_month
            )
            win = _pick_overflow_goal_index(goals, ordered_indices, cand)
            if win is not None:
                alloc_this_month[win] += remaining_surplus
                remaining_surplus = 0.0

        # Rebalance: move allocation from goals above amortized need to goals below need
        # (nearest deadline first), so overflow does not starve a nearer-dated goal.
        need_by_idx: dict[int, float] = {}
        for ii in range(n_goals):
            if completed[ii]:
                continue
            gg = goals[ii]
            gcls = gg.goal_class.upper()
            if gcls == GC_RECURRING:
                if not _recurring_is_active(gg, current_month):
                    need_by_idx[ii] = 0.0
                else:
                    need_by_idx[ii] = _recurring_monthly_need_for_month(
                        gg,
                        current_month,
                        start_month,
                        params.general_inflation_rate,
                    )
            elif gcls == GC_POINT:
                nd = _pit_dynamic_need(
                    gg,
                    m,
                    current_month,
                    current_value[ii],
                    params.general_inflation_rate,
                    params.salary_growth_rate,
                )
                ccap = ceilings.get(ii)
                if ccap is not None:
                    nd = min(nd, ccap)
                need_by_idx[ii] = nd
            else:
                need_by_idx[ii] = 0.0
        _redistribute_excess_to_shortfalls(goals, alloc_this_month, need_by_idx, completed)
        spill_floor = _apply_minimum_monthly_contribution_floor(
            goals, alloc_this_month, ordered_indices, completed, current_month
        )

        sum_unallocated += max(0.0, remaining_surplus) + spill_floor

        monthly_pool = max(0.0, active_surplus + extra)
        allocated_total = sum(float(alloc_this_month.get(i, 0.0)) for i in range(n_goals))
        unallocated_this_month = max(0.0, monthly_pool - allocated_total)

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
                if params.salary_growth_rate > 0:
                    tgt_snap = compute_target_at_month_with_growing_contributions(
                        g,
                        m + 1,
                        steady_pmt[i],
                        params.salary_growth_rate,
                    )
                else:
                    tgt_snap = compute_target_at_month(g, m + 1, steady_pmt[i])

            mn = 0.0
            if not completed[i]:
                mn = float(need_by_idx.get(i, 0.0))

            trajectories[i].append(
                MonthlySnapshot(
                    month=current_month,
                    cumulative_value=round(current_value[i], 2),
                    monthly_contribution=round(contrib, 2),
                    monthly_return=round(ret, 2),
                    target_at_month=round(tgt_snap, 2) if tgt_snap is not None else None,
                    monthly_need=round(mn, 2) if mn > 1e-12 else None,
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
                        if j != i and not completed[j]
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
                monthly_surplus_pool=round(monthly_pool, 2),
                unallocated_surplus=round(unallocated_this_month, 2),
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

        shortfall = 0.0
        p_pct: float | None = None
        p_m_pct: float | None = None
        corpus_d: float | None = None
        infl_t_d: float | None = None
        shortfall_at_dl: float | None = None
        wdef: float | None = None
        periods_total: int | None = None
        periods_funded: int | None = None
        funding_rate: float | None = None
        total_contributed: float | None = None
        total_needed: float | None = None

        if (
            g.goal_class.upper() == GC_POINT
            and g.target_amount is not None
            and g.target_date is not None
        ):
            cps, it, sh_at_dead, pct, end_gap = _pit_deadline_financials(
                g,
                trajectories[i],
                start_month,
                sim_months,
                params.general_inflation_rate,
            )
            p_pct = pct
            shortfall = end_gap
            corpus_d = cps
            infl_t_d = it
            shortfall_at_dl = sh_at_dead
        elif g.goal_class.upper() == GC_RECURRING:
            periods_total, periods_funded, funding_rate, total_contributed, total_needed, wdef = (
                _compute_recurring_funding_stats(g, trajectories[i])
            )
            shortfall = max(0.0, (total_needed or 0.0) - (total_contributed or 0.0))
            p_m_pct = (
                round(100.0 * (periods_funded or 0) / (periods_total or 1), 2)
                if (periods_total or 0) > 0
                else None
            )
        else:
            shortfall = 0.0

        projections.append(
            GoalProjection(
                goal_id=g.id,
                goal_name=g.name,
                monthly_allocation=round(avg_alloc, 2),
                projected_completion_date=completion_date[i],
                projected_completion_pct=p_pct,
                periods_met_pct=p_m_pct,
                corpus_at_deadline=corpus_d,
                inflation_adjusted_target_at_deadline=infl_t_d,
                shortfall_at_deadline=shortfall_at_dl,
                worst_period_deficit=wdef,
                projected_final_amount=round(final_amt, 2),
                shortfall=round(shortfall, 2),
                monthly_trajectory=trajectories[i],
                periods_total=periods_total,
                periods_funded=periods_funded,
                funding_rate=funding_rate,
                total_contributed=total_contributed,
                total_needed=total_needed,
            )
        )

    projections.sort(key=lambda p: (not _projection_fully_successful(p), p.goal_name))

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


def simulate(params: SimulationParams) -> SimulationResult:
    """Month-by-month projection with cascade-aware PMT refinement.

    Runs an initial unconstrained pass, then derives PMT *ceilings* for lower-priority
    POINT_IN_TIME goals that completed before their deadline while a higher-priority
    PIT was still open. Re-simulates with those caps until ceilings stabilize (within
    1% relative) or :data:`MAX_REFINEMENT_PASSES` is reached.

    **When results look unchanged:** refinement only runs if at least one PIT goal has
    ``projected_completion_date`` set (reached the inflated target in-sim) *before* the
    goal’s ``target_date`` month. Otherwise ``_compute_pmt_ceilings`` returns ``{}`` and
    only the first inner pass runs (same as pre-refinement behavior).

    **Debug:** set ``ARTH_SIMULATION_DEBUG=1`` or ``arth_simulation_debug=1`` in the API
    process environment (e.g. root ``.env`` loaded by ``python-dotenv``) and restart
    uvicorn. Logs go to ``data/logs/arth.log`` at DEBUG (stdout stays INFO unless you
    lower the stream level).
    """
    params = params.model_copy(update={"goals": list(params.goals)})

    dbg = _simulation_debug_enabled()
    if dbg:
        logger.debug(
            "simulate start: goals=%d simulation_months=%d",
            len(params.goals),
            params.simulation_months,
        )

    result = _simulate_inner(params, pmt_ceilings=None)
    if dbg:
        _log_simulation_debug_pit_snapshot("after pass 1 (no ceilings)", params, result)

    prev_ceilings: dict[int, float] = {}
    pass_num = 1

    for _ in range(MAX_REFINEMENT_PASSES - 1):
        ceilings = _compute_pmt_ceilings(params, result)
        if not ceilings:
            if dbg:
                logger.debug(
                    "simulate refinement: no ceilings (no eligible PIT — need early completion "
                    "with completion month strictly before target month, plus predecessor PIT). "
                    "passes_used=%d",
                    pass_num,
                )
            break
        if dbg:
            # Index-keyed ceilings avoid logging goal titles (user-chosen, often personally identifying).
            ceiling_by_idx = {
                i: round(v, 2)
                for i, v in ceilings.items()
                if 0 <= i < len(params.goals)
            }
            logger.debug(
                "simulate refinement: computed ceilings INR/month by_goal_idx=%s",
                ceiling_by_idx,
            )

        # First refinement: prev is empty — must apply ceilings once (do not treat as converged).
        if prev_ceilings and _ceilings_converged(prev_ceilings, ceilings):
            if dbg:
                logger.debug(
                    "simulate refinement: ceilings converged within %.0f%% — passes_used=%d",
                    _CEILING_REL_TOL * 100,
                    pass_num,
                )
            break
        result = _simulate_inner(params, pmt_ceilings=ceilings)
        pass_num += 1
        if dbg:
            _log_simulation_debug_pit_snapshot(f"after pass {pass_num} (with ceilings)", params, result)
        prev_ceilings = ceilings

    if dbg:
        logger.debug("simulate done: total_inner_passes=%d", pass_num)

    return result


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

            b_pct = _headline_sim_progress_pct(bp)
            v_pct = _headline_sim_progress_pct(vp)
            deltas.append(
                GoalDelta(
                    goal_name=gn,
                    base_completion=bcd,
                    variant_completion=vcd,
                    base_progress_pct=b_pct,
                    variant_progress_pct=v_pct,
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


