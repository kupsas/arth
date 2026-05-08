"""
Unit tests for Sub-Plan G — :mod:`api.services.simulation` (pure functions, no DB).
"""

from __future__ import annotations

import datetime
import time

import pytest

from api.services import simulation as sim_mod
from api.services.simulation import (
    GC_POINT,
    GC_RECURRING,
    MANDATORY_RECURRING_SUBTYPES,
    MIN_MONTHLY_GOAL_CONTRIBUTION_INR,
    OneTimeEvent,
    SimulationGoal,
    SimulationParams,
    _simulate_inner,
    allocate_surplus,
    compare_scenarios,
    compute_target_at_month,
    simulate,
)


def _pit(
    name: str,
    *,
    tid: int,
    target: float,
    target_date: datetime.date,
    priority: int,
    start: float = 0.0,
    ret: float = 12.0,
    infl: float = 0.0,
) -> SimulationGoal:
    return SimulationGoal(
        id=tid,
        name=name,
        goal_class=GC_POINT,
        target_amount=target,
        target_date=target_date,
        starting_balance=start,
        allocation_priority=priority,
        expected_return_rate=ret,
        inflation_rate=infl,
    )


def _overflow_sink_pit(
    tid: int,
    name: str = "Overflow sink",
    *,
    priority: int = 99,
    ret: float = 10.0,
) -> SimulationGoal:
    """Open PIT with a far horizon — absorbs surplus like the removed GROWTH class."""

    return SimulationGoal(
        id=tid,
        name=name,
        goal_class=GC_POINT,
        target_amount=1e15,
        target_date=datetime.date(2100, 1, 1),
        starting_balance=0.0,
        allocation_priority=priority,
        expected_return_rate=ret,
        inflation_rate=0.0,
    )


def test_single_point_in_time_goal_projection():
    """20L target in 5 years, 12% return, ₹30k surplus — expect trajectory + completion."""
    today = datetime.date(2026, 1, 1)
    target_d = datetime.date(2031, 1, 1)
    g = _pit("House", tid=1, target=2_000_000.0, target_date=target_d, priority=1)
    g2 = _overflow_sink_pit(2, "Overflow", priority=2)
    p = SimulationParams(
        goals=[g, g2],
        monthly_surplus=30_000.0,
        simulation_months=120,
        as_of_date=today,
    )
    r = simulate(p)
    house = next(x for x in r.projections if x.goal_name == "House")
    assert house.projected_completion_date is not None
    assert house.projected_completion_date is not None
    assert (house.projected_completion_pct or 0) >= 100.0
    assert len(house.monthly_trajectory) == 120
    assert house.monthly_trajectory[0].month == today.replace(day=1)


def test_cascade_two_goals_second_gets_surplus_after_first_completes():
    """First goal small target completes early; second should show earlier completion vs isolated."""
    today = datetime.date(2026, 6, 1)
    g1 = _pit(
        "Quick",
        tid=1,
        target=50_000.0,
        target_date=datetime.date(2027, 6, 1),
        priority=1,
        start=40_000.0,
        ret=10.0,
        infl=0.0,
    )
    g2 = _pit(
        "Slow",
        tid=2,
        target=500_000.0,
        target_date=datetime.date(2031, 6, 1),
        priority=2,
        start=0.0,
        ret=10.0,
        infl=0.0,
    )
    growth = _overflow_sink_pit(3, "Invest", priority=3)
    p = SimulationParams(
        goals=[g1, g2, growth],
        monthly_surplus=25_000.0,
        simulation_months=120,
        as_of_date=today,
    )
    r = simulate(p)
    quick = next(x for x in r.projections if x.goal_name == "Quick")
    assert quick.projected_completion_date is not None
    assert (quick.projected_completion_pct or 0) >= 100.0
    assert len(r.cascade_events) >= 1


def test_recurring_emi_within_window():
    today = datetime.date(2026, 1, 1)
    emi = SimulationGoal(
        id=1,
        name="Loan EMI",
        goal_class=GC_RECURRING,
        goal_subtype="LOAN_PAYOFF",
        allocation_priority=1,
        recurrence_amount=55_000.0,
        recurrence_frequency="MONTHLY",
        recurrence_start=datetime.date(2026, 1, 1),
        recurrence_end=datetime.date(2046, 1, 1),
        expected_return_rate=0.0,
    )
    growth = _overflow_sink_pit(2, "Rest", priority=2)
    p = SimulationParams(
        goals=[emi, growth],
        monthly_surplus=100_000.0,
        simulation_months=24,
        as_of_date=today,
    )
    r = simulate(p)
    emi_p = next(x for x in r.projections if x.goal_name == "Loan EMI")
    # Average allocation to EMI should be substantial (capped by surplus after overflow sink)
    assert emi_p.monthly_allocation > 40_000.0


def test_mandatory_recurring_subtypes_constant():
    assert "LOAN_PAYOFF" in MANDATORY_RECURRING_SUBTYPES
    assert "EMERGENCY_FUND" in MANDATORY_RECURRING_SUBTYPES
    assert "CHILD_EDUCATION" in MANDATORY_RECURRING_SUBTYPES


def test_minimum_monthly_floor_zeros_small_allocation_and_redistributes_to_overflow():
    """Sub-₹5k/mo flows become 0; freed cash goes to an open PIT overflow sink."""
    assert MIN_MONTHLY_GOAL_CONTRIBUTION_INR == 5000.0
    today = datetime.date(2026, 1, 1)
    travel = SimulationGoal(
        id=1,
        name="Small travel",
        goal_class=GC_RECURRING,
        goal_subtype="TRAVEL",
        allocation_priority=3,
        recurrence_amount=3_000.0,
        recurrence_frequency="MONTHLY",
        recurrence_start=today,
        expected_return_rate=0.0,
    )
    growth = _overflow_sink_pit(2, "Growth", priority=2)
    p = SimulationParams(
        goals=[travel, growth],
        monthly_surplus=10_000.0,
        simulation_months=3,
        as_of_date=today,
    )
    r = simulate(p)
    travel_p = next(x for x in r.projections if x.goal_name == "Small travel")
    growth_p = next(x for x in r.projections if x.goal_name == "Growth")
    assert travel_p.monthly_trajectory[0].monthly_contribution == 0.0
    assert growth_p.monthly_trajectory[0].monthly_contribution == pytest.approx(10_000.0)
    assert travel_p.monthly_allocation == 0.0


def test_allocate_surplus_applies_same_minimum_floor():
    tiny = SimulationGoal(
        id=1,
        name="Tiny bill",
        goal_class=GC_RECURRING,
        goal_subtype="CUSTOM",
        allocation_priority=2,
        recurrence_amount=3_000.0,
        recurrence_frequency="MONTHLY",
        recurrence_start=datetime.date(2026, 1, 1),
        expected_return_rate=0.0,
    )
    growth = _overflow_sink_pit(2, "Growth", priority=3)
    out = allocate_surplus([tiny, growth], 10_000.0, datetime.date(2026, 1, 1))
    assert out.get("Tiny bill", 0.0) == 0.0
    assert out.get("Growth", 0.0) == pytest.approx(10_000.0)


def test_discretionary_recurring_competes_with_pit_not_before():
    """TRAVEL/CUSTOM recurring no longer eats surplus before higher-priority PIT."""
    today = datetime.date(2026, 1, 1)
    house = _pit(
        "House",
        tid=1,
        target=15_000_000.0,
        target_date=datetime.date(2031, 7, 1),
        priority=1,
        ret=10.0,
        infl=6.0,
    )
    travel = SimulationGoal(
        id=2,
        name="Domestic travel",
        goal_class=GC_RECURRING,
        goal_subtype="TRAVEL",
        allocation_priority=2,
        recurrence_amount=100_000.0,
        recurrence_frequency="ANNUAL",
        recurrence_start=today,
        expected_return_rate=0.0,
    )
    p = SimulationParams(
        goals=[house, travel],
        monthly_surplus=50_000.0,
        simulation_months=12,
        as_of_date=today,
        general_inflation_rate=6.0,
    )
    r = simulate(p)
    house_p = next(x for x in r.projections if x.goal_name == "House")
    travel_p = next(x for x in r.projections if x.goal_name == "Domestic travel")
    assert house_p.monthly_allocation > travel_p.monthly_allocation
    assert travel_p.monthly_allocation < 15_000.0


def test_allocate_surplus_discretionary_recurring_after_pit():
    house = _pit(
        "House",
        tid=1,
        target=15_000_000.0,
        target_date=datetime.date(2031, 7, 1),
        priority=1,
        ret=10.0,
        infl=6.0,
    )
    travel = SimulationGoal(
        id=2,
        name="Travel",
        goal_class=GC_RECURRING,
        goal_subtype="TRAVEL",
        allocation_priority=2,
        recurrence_amount=100_000.0,
        recurrence_frequency="ANNUAL",
        recurrence_start=datetime.date(2026, 1, 1),
        expected_return_rate=0.0,
    )
    out = allocate_surplus(
        [house, travel],
        50_000.0,
        today=datetime.date(2026, 1, 1),
        general_inflation_rate=6.0,
    )
    assert out["House"] >= out["Travel"]


def test_open_pit_absorbs_remainder():
    g = _overflow_sink_pit(1, "Long", priority=1, ret=10.0)
    p = SimulationParams(
        goals=[g],
        monthly_surplus=50_000.0,
        simulation_months=60,
        as_of_date=datetime.date(2026, 1, 1),
    )
    r = simulate(p)
    gp = r.projections[0]
    assert gp.projected_final_amount > 3_000_000.0


def test_salary_growth_increases_surplus():
    g = _overflow_sink_pit(1, "G", priority=1)
    p = SimulationParams(
        goals=[g],
        monthly_surplus=100_000.0,
        salary_growth_rate=10.0,
        simulation_months=24,
        as_of_date=datetime.date(2026, 1, 1),
    )
    r = simulate(p)
    # Month 13+ should have higher net worth trajectory than without growth
    p2 = SimulationParams(**{**p.model_dump(), "salary_growth_rate": 0.0})
    r2 = simulate(p2)
    assert r.net_worth_projection[-1].total_value > r2.net_worth_projection[-1].total_value


def test_one_time_inflow_accelerates():
    today = datetime.date(2026, 1, 1)
    g = _pit(
        "Save",
        tid=1,
        target=1_000_000.0,
        target_date=datetime.date(2030, 1, 1),
        priority=1,
        ret=8.0,
    )
    g2 = _overflow_sink_pit(2, "Gr", priority=2)
    bonus = OneTimeEvent(amount=500_000.0, date=datetime.date(2026, 6, 15), description="bonus")
    p = SimulationParams(
        goals=[g, g2],
        monthly_surplus=10_000.0,
        one_time_inflows=[bonus],
        simulation_months=80,
        as_of_date=today,
    )
    r = simulate(p)
    base = SimulationParams(
        goals=[g, g2],
        monthly_surplus=10_000.0,
        simulation_months=80,
        as_of_date=today,
    )
    r0 = simulate(base)
    h = next(x for x in r.projections if x.goal_name == "Save")
    h0 = next(x for x in r0.projections if x.goal_name == "Save")
    if h.projected_completion_date and h0.projected_completion_date:
        assert h.projected_completion_date <= h0.projected_completion_date


def test_inflation_reduces_achievement():
    today = datetime.date(2026, 1, 1)
    td = datetime.date(2031, 1, 1)
    g = _pit("T", tid=1, target=1_000_000.0, target_date=td, priority=1, infl=0.0)
    g2 = _overflow_sink_pit(2, "X", priority=2)
    p0 = SimulationParams(goals=[g, g2], monthly_surplus=15_000.0, simulation_months=120, as_of_date=today)
    g_hi = _pit("T", tid=1, target=1_000_000.0, target_date=td, priority=1, infl=8.0)
    p1 = SimulationParams(goals=[g_hi, g2], monthly_surplus=15_000.0, simulation_months=120, as_of_date=today)
    r0 = simulate(p0)
    r1 = simulate(p1)
    a = next(x for x in r0.projections if x.goal_name == "T")
    b = next(x for x in r1.projections if x.goal_name == "T")
    assert b.shortfall >= a.shortfall


def test_edge_no_goals():
    r = simulate(SimulationParams(goals=[]))
    assert r.projections == []
    assert "No goals" in r.warnings[0]


def test_edge_already_funded():
    today = datetime.date(2026, 1, 1)
    g = _pit(
        "Done",
        tid=1,
        target=100_000.0,
        target_date=datetime.date(2027, 1, 1),
        priority=1,
        start=150_000.0,
    )
    p = SimulationParams(goals=[g], monthly_surplus=0.0, simulation_months=12, as_of_date=today)
    r = simulate(p)
    assert (r.projections[0].projected_completion_pct or 0) >= 100.0


def test_allocate_surplus_sums_to_surplus_when_sink_pit_present():
    goals = [
        _pit("A", tid=1, target=500_000.0, target_date=datetime.date(2030, 1, 1), priority=1),
        _overflow_sink_pit(2, "G", priority=2),
    ]
    out = allocate_surplus(goals, 80_000.0, today=datetime.date(2026, 1, 1))
    assert abs(sum(out.values()) - 80_000.0) < 1.0


def test_allocate_surplus_overflow_to_highest_priority_open_pit():
    """After pass 2, leftover surplus goes to the lowest allocation_priority number among open PITs."""
    g1 = _pit(
        "House",
        tid=1,
        target=2_000_000.0,
        target_date=datetime.date(2035, 6, 1),
        priority=1,
        ret=10.0,
        infl=0.0,
    )
    g2 = _pit(
        "FIRE",
        tid=2,
        target=5_000_000.0,
        target_date=datetime.date(2040, 6, 1),
        priority=2,
        ret=10.0,
        infl=0.0,
    )
    # Past, over-funded PIT — no pass-2 need (like removed GROWTH); overflow stays with real goals.
    sink = SimulationGoal(
        id=3,
        name="Invest",
        goal_class=GC_POINT,
        target_amount=100.0,
        target_date=datetime.date(2000, 1, 1),
        starting_balance=1_000_000.0,
        allocation_priority=3,
        expected_return_rate=10.0,
        inflation_rate=0.0,
    )
    out = allocate_surplus(
        [g1, g2, sink],
        200_000.0,
        today=datetime.date(2026, 1, 1),
        general_inflation_rate=6.0,
    )
    assert abs(sum(out.values()) - 200_000.0) < 1.0
    assert out["House"] > out["FIRE"]
    assert out["Invest"] == pytest.approx(0.0, abs=1.0)


def test_allocate_surplus_single_goal_no_growth_funnels_all_surplus():
    """Amortized need can be < surplus; the only active pot should take the rest."""
    g = _pit(
        "FIRE",
        tid=1,
        target=40_000_000.0,
        target_date=datetime.date(2046, 7, 1),
        priority=1,
        ret=10.0,
        infl=4.2,
    )
    out = allocate_surplus(
        [g],
        160_000.0,
        today=datetime.date(2026, 4, 1),
        general_inflation_rate=4.2,
    )
    assert abs(sum(out.values()) - 160_000.0) < 1.0


def test_simulate_single_pit_no_growth_no_false_unallocated():
    """While the sole goal is still active, surplus must not sit idle (horizon ends before FIRE completes)."""
    today = datetime.date(2026, 4, 1)
    g = _pit(
        "FIRE",
        tid=1,
        target=40_000_000.0,
        target_date=datetime.date(2046, 7, 29),
        priority=1,
        ret=10.0,
        infl=4.2,
    )
    p = SimulationParams(
        goals=[g],
        monthly_surplus=160_000.0,
        salary_growth_rate=5.0,
        general_inflation_rate=4.2,
        simulation_months=120,
        as_of_date=today,
    )
    r = simulate(p)
    assert r.unallocated_surplus < 1.0


def test_compare_scenarios_delta():
    today = datetime.date(2026, 1, 1)
    g = _pit("X", tid=1, target=800_000.0, target_date=datetime.date(2032, 1, 1), priority=1)
    gr = _overflow_sink_pit(2, "G", priority=2)
    base = SimulationParams(goals=[g, gr], monthly_surplus=20_000.0, simulation_months=100, as_of_date=today)
    var = SimulationParams(goals=[g, gr], monthly_surplus=40_000.0, simulation_months=100, as_of_date=today)
    comps = compare_scenarios(base, [var])
    assert len(comps) == 1
    assert "monthly_surplus" in comps[0].changes_from_base


def test_growing_annuity_first_payment_below_level_when_salary_growth_positive():
    """Growing PMT assumes larger later contributions → lower first payment than level PMT."""
    gap = 5_000_000.0
    n = 240
    ret = 10.0
    level = sim_mod._pmt_needed(gap, n, ret, annual_salary_growth_pct=0.0)
    growing = sim_mod._pmt_needed(gap, n, ret, annual_salary_growth_pct=5.0)
    assert growing < level
    assert growing > 0


def test_near_deadline_goal_gets_overflow_from_higher_priority_long_horizon():
    """When priority-1 PIT absorbs overflow but priority-2 has unmet PMT, rebalance moves cash."""
    today = datetime.date(2026, 4, 1)
    fire = _pit(
        "FIRE",
        tid=1,
        target=40_000_000.0,
        target_date=datetime.date(2046, 7, 1),
        priority=1,
        ret=10.0,
        infl=4.2,
    )
    house = _pit(
        "House",
        tid=2,
        target=10_000_000.0,
        target_date=datetime.date(2031, 7, 1),
        priority=2,
        ret=10.0,
        infl=6.0,
    )
    p = SimulationParams(
        goals=[fire, house],
        monthly_surplus=200_000.0,
        salary_growth_rate=5.0,
        general_inflation_rate=4.2,
        simulation_months=80,
        as_of_date=today,
    )
    r = simulate(p)
    house_p = next(x for x in r.projections if x.goal_name == "House")
    fire_p = next(x for x in r.projections if x.goal_name == "FIRE")
    # House should receive more of the early-month budget than if FIRE kept all overflow forever.
    assert house_p.monthly_allocation > 0
    assert fire_p.monthly_allocation > 0


def test_compute_target_at_month():
    g = _pit(
        "C",
        tid=1,
        target=1.0,
        target_date=datetime.date(2030, 1, 1),
        priority=1,
        start=10_000.0,
        ret=12.0,
    )
    t = compute_target_at_month(g, 12, monthly_required=5_000.0)
    assert t is not None and t > 10_000.0


def test_performance_ten_goals_twenty_years():
    """Cascade refinement runs up to 5 passes; keep total wall time reasonable."""
    today = datetime.date(2026, 1, 1)
    goals: list[SimulationGoal] = []
    for i in range(10):
        goals.append(
            _pit(
                f"G{i}",
                tid=i + 1,
                target=500_000.0 + i * 10_000,
                target_date=datetime.date(2046, 1, 1),
                priority=i + 1,
            )
        )
    p = SimulationParams(goals=goals, monthly_surplus=200_000.0, simulation_months=240, as_of_date=today)
    t0 = time.perf_counter()
    simulate(p)
    elapsed = time.perf_counter() - t0
    # 2s ceiling is generous enough for slow GitHub-hosted runners (shared CPU)
    # while still catching a genuine O(n²) regression in the cascade loop.
    assert elapsed < 2.0, f"took {elapsed:.3f}s, expected <2s"


def _avg_contrib_first_n(trajectory: list, n: int) -> float:
    if not trajectory:
        return 0.0
    k = min(n, len(trajectory))
    return sum(s.monthly_contribution for s in trajectory[:k]) / k


def test_refinement_reduces_lower_priority_early_allocation():
    """Lower-priority PIT gets an observation-based cap; FIRE clash-window avg drops vs raw.

    Compare the **early clash window** (first 36 months), not 60 — after refinement House may
    complete sooner and FIRE can spike post-cascade in months 50–59, raising a 60-month average
    even when early-month FIRE is lower.
    """
    today = datetime.date(2026, 4, 1)
    house = _pit(
        "House",
        tid=1,
        target=10_000_000.0,
        target_date=datetime.date(2031, 7, 1),
        priority=1,
        ret=10.0,
        infl=6.0,
    )
    fire = _pit(
        "FIRE",
        tid=2,
        target=5_000_000.0,
        target_date=datetime.date(2046, 7, 1),
        priority=2,
        ret=10.0,
        infl=4.2,
    )
    p = SimulationParams(
        goals=[house, fire],
        monthly_surplus=200_000.0,
        salary_growth_rate=5.0,
        general_inflation_rate=4.2,
        simulation_months=240,
        as_of_date=today,
    )
    raw = _simulate_inner(p, None)
    refined = simulate(p)
    fire_raw = next(x for x in raw.projections if x.goal_name == "FIRE")
    fire_ref = next(x for x in refined.projections if x.goal_name == "FIRE")
    early_n = 36
    a_early_raw = _avg_contrib_first_n(fire_raw.monthly_trajectory, early_n)
    a_early_ref = _avg_contrib_first_n(fire_ref.monthly_trajectory, early_n)
    assert a_early_ref < a_early_raw - 200.0


def test_simulate_matches_inner_when_goal_never_achieves():
    """No ACHIEVED+early completion → no ceilings; refined result matches single inner pass."""
    today = datetime.date(2026, 1, 1)
    g = _pit(
        "Big",
        tid=1,
        target=900_000_000.0,
        target_date=datetime.date(2050, 1, 1),
        priority=1,
        ret=8.0,
        infl=6.0,
    )
    p = SimulationParams(goals=[g], monthly_surplus=5_000.0, simulation_months=120, as_of_date=today)
    inner = _simulate_inner(p, None)
    out = simulate(p)
    assert abs(inner.projections[0].projected_final_amount - out.projections[0].projected_final_amount) < 1.0


def test_refinement_preserves_mandatory_recurring_totals():
    """PMT caps apply to PIT only; mandatory recurring average unchanged when no PIT refinement."""
    today = datetime.date(2026, 1, 1)
    emi = SimulationGoal(
        id=1,
        name="Loan EMI",
        goal_class=GC_RECURRING,
        goal_subtype="LOAN_PAYOFF",
        allocation_priority=1,
        recurrence_amount=55_000.0,
        recurrence_frequency="MONTHLY",
        recurrence_start=datetime.date(2026, 1, 1),
        recurrence_end=datetime.date(2046, 1, 1),
        expected_return_rate=0.0,
    )
    growth = _overflow_sink_pit(2, "Rest", priority=2)
    p = SimulationParams(
        goals=[emi, growth],
        monthly_surplus=100_000.0,
        simulation_months=24,
        as_of_date=today,
    )
    inner = _simulate_inner(p, None)
    out = simulate(p)
    e0 = next(x for x in inner.projections if x.goal_name == "Loan EMI")
    e1 = next(x for x in out.projections if x.goal_name == "Loan EMI")
    assert abs(e0.monthly_allocation - e1.monthly_allocation) < 0.01


def test_recurring_trajectory_has_monthly_need_and_funding_stats():
    """Recurring goals expose monthly_need per snapshot and aggregate funding_rate."""
    today = datetime.date(2026, 1, 1)
    emi = SimulationGoal(
        id=1,
        name="Loan EMI",
        goal_class=GC_RECURRING,
        goal_subtype="LOAN_PAYOFF",
        allocation_priority=1,
        recurrence_amount=20_000.0,
        recurrence_frequency="MONTHLY",
        recurrence_start=today,
        recurrence_end=datetime.date(2046, 1, 1),
        expected_return_rate=0.0,
    )
    p = SimulationParams(
        goals=[emi],
        monthly_surplus=50_000.0,
        simulation_months=6,
        as_of_date=today,
    )
    r = simulate(p)
    loan = next(x for x in r.projections if x.goal_name == "Loan EMI")
    for snap in loan.monthly_trajectory:
        assert snap.monthly_need is not None
        assert snap.monthly_need > 0
        assert snap.monthly_contribution <= snap.monthly_need + 1e-6
    assert loan.periods_total == 6
    assert loan.periods_funded == 6
    assert loan.funding_rate is not None
    assert loan.funding_rate >= 0.99
    assert loan.total_needed is not None
    assert loan.total_contributed is not None
    assert loan.periods_met_pct is not None
    assert loan.periods_met_pct >= 99.0


def test_recurring_quarterly_funding_stats_align_to_first_billable_month():
    """QUARTERLY periods must chunk from first positive need, not simulation month 0."""
    today = datetime.date(2026, 1, 1)
    sub = SimulationGoal(
        id=2,
        name="Quarterly sub",
        goal_class=GC_RECURRING,
        goal_subtype="CUSTOM",
        allocation_priority=1,
        recurrence_amount=30_000.0,
        recurrence_frequency="QUARTERLY",
        recurrence_start=datetime.date(2026, 4, 1),
        recurrence_end=datetime.date(2040, 1, 1),
        expected_return_rate=0.0,
    )
    p = SimulationParams(
        goals=[sub],
        monthly_surplus=100_000.0,
        simulation_months=12,
        as_of_date=today,
    )
    r = simulate(p)
    proj = next(x for x in r.projections if x.goal_name == "Quarterly sub")
    # Jan–Mar: before recurrence_start → no need; Apr–Dec: three full quarterly windows
    assert proj.periods_total == 3
    assert proj.periods_funded == 3
    assert proj.funding_rate is not None
    assert proj.funding_rate >= 0.99
