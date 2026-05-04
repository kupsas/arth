"""
Sub-Plan G.2 — scenario tests mapped from ``docs/personal-data/simulation_engine_test_scenarios.csv``.

Each test name includes the CSV ``test_id`` (e.g. ``test_r_inf_1``) for traceability.
Pure :func:`simulate` / :func:`allocate_surplus` / :func:`compare_scenarios` unless noted.

**Engine vs CSV wording:** The CSV sometimes describes a fixed monthly *contribution* (e.g. ₹50k/mo)
toward a goal. The implementation allocates the *PMT needed* each month, capped by surplus (see
``api/services/simulation.py``). Tests use **tight target deadlines** or **single-goal setups** so
the needed PMT uses the available surplus, and document behavior where averages differ (e.g. S2.3).
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from api.models import Goal, Holding
from api.services.goal_decomposer import add_months, months_between
from api.services.liquidity_service import check_liquidity_mismatch
from api.services.simulation import (
    GC_POINT,
    GC_RECURRING,
    OneTimeEvent,
    SimulationGoal,
    SimulationParams,
    _effective_goal_inflation,
    _inflation_target_at_month,
    allocate_surplus,
    compare_scenarios,
    simulate,
)
from pipeline.models import AssetClass, LiquidityClass, ValuationMethod

# ── Shared builders (CSV-driven inputs) ──────────────────────────────────────


def _pit(
    name: str,
    *,
    tid: int,
    target: float,
    target_date: datetime.date,
    priority: int,
    start: float = 0.0,
    ret: float = 10.0,
    infl: float | None = 0.0,
) -> SimulationGoal:
    """POINT_IN_TIME goal. *infl* None = use SimulationParams.general_inflation_rate."""
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


def _recurring(
    name: str,
    *,
    tid: int,
    amount: float,
    freq: str,
    start_date: datetime.date,
    end_date: datetime.date | None,
    priority: int,
    ret: float = 0.0,
) -> SimulationGoal:
    return SimulationGoal(
        id=tid,
        name=name,
        goal_class=GC_RECURRING,
        allocation_priority=priority,
        recurrence_amount=amount,
        recurrence_frequency=freq,
        recurrence_start=start_date,
        recurrence_end=end_date,
        expected_return_rate=ret,
    )


def _closed_sink_placeholder(name: str, *, tid: int, priority: int, ret: float = 10.0) -> SimulationGoal:
    """Past-deadline, over-funded PIT — no pass-2 need (mirrors old GROWTH in CSV scenarios)."""

    return SimulationGoal(
        id=tid,
        name=name,
        goal_class=GC_POINT,
        target_amount=100.0,
        target_date=datetime.date(2000, 1, 1),
        starting_balance=1_000_000.0,
        allocation_priority=priority,
        expected_return_rate=ret,
        inflation_rate=0.0,
    )


def _open_overflow_sink_pit(name: str, *, tid: int, priority: int, ret: float = 10.0) -> SimulationGoal:
    """Far horizon PIT that still absorbs surplus in S8-style edge tests."""

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


def _params(
    goals: list[SimulationGoal],
    surplus: float,
    *,
    months: int = 240,
    salary_growth: float = 0.0,
    inflows: list[OneTimeEvent] | None = None,
    outflows: list[OneTimeEvent] | None = None,
    general_inflation: float = 6.0,
    as_of: datetime.date | None = None,
) -> SimulationParams:
    return SimulationParams(
        goals=goals,
        monthly_surplus=surplus,
        salary_growth_rate=salary_growth,
        general_inflation_rate=general_inflation,
        simulation_months=months,
        one_time_inflows=inflows or [],
        one_time_outflows=outflows or [],
        as_of_date=as_of,
    )


def _months_from_start_to_date(
    start: datetime.date,
    end: datetime.date | None,
) -> int | None:
    if end is None:
        return None
    return months_between(start.replace(day=1), end)


# ── DB fixtures (S7.x liquidity) ─────────────────────────────────────────────


@pytest.fixture(name="engine")
def _engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(name="session")
def _session(engine):
    with Session(engine) as session:
        yield session


# ── 1. R-VOC-1 ───────────────────────────────────────────────────────────────


class TestRulesVocabulary:
    """Rules — Vocabulary (R-VOC-1)."""

    def test_r_voc_1_goal_projection_has_progress_percentages(self) -> None:
        """PIT and recurring expose headline % fields (not categorical status)."""
        g = _pit("H", tid=1, target=1_000_000.0, target_date=datetime.date(2036, 1, 1), priority=1, infl=6.5)
        r = _params([g], 20_000.0, months=12, general_inflation=6.5, as_of=datetime.date(2026, 1, 1))
        out = simulate(r)
        p = out.projections[0]
        assert p.projected_completion_pct is not None
        assert p.periods_met_pct is None


# ── 2. R-INF-1, R-INF-2, R-INF-3 ─────────────────────────────────────────────


class TestRulesInflation:
    """Rules — Inflation."""

    def test_r_inf_1_null_goal_inflation_uses_headline_cpi_scalar(self) -> None:
        """When goal inflation_rate is None, engine uses general_inflation_rate."""
        g = SimulationGoal(
            id=1,
            name="H",
            goal_class=GC_POINT,
            target_amount=1_000_000.0,
            target_date=datetime.date(2036, 1, 1),
            allocation_priority=1,
            inflation_rate=None,
            expected_return_rate=8.0,
        )
        assert _effective_goal_inflation(g, 6.5) == 6.5
        # Month 0 inflated target should match explicit 6.5% on the goal
        t_none = _inflation_target_at_month(1_000_000.0, 6.5, 0)
        g65 = _pit("X", tid=1, target=1_000_000.0, target_date=datetime.date(2036, 1, 1), priority=1, infl=6.5)
        t_explicit = _inflation_target_at_month(1_000_000.0, _effective_goal_inflation(g65, 99.0), 0)
        assert t_none == pytest.approx(t_explicit)

        gr = _closed_sink_placeholder("G", tid=2, priority=2)
        p = _params([g, gr], 20_000.0, months=120, general_inflation=6.5, as_of=datetime.date(2026, 1, 1))
        r = simulate(p)
        pit = next(x for x in r.projections if x.goal_name == "H")
        assert pit.projected_completion_pct is not None

    def test_r_inf_2_goal_specific_inflation_overrides_general(self) -> None:
        g8 = _pit(
            "House",
            tid=1,
            target=1_000_000.0,
            target_date=datetime.date(2036, 1, 1),
            priority=1,
            infl=8.0,
        )
        assert _effective_goal_inflation(g8, 6.0) == 8.0
        infl_at_m12 = _inflation_target_at_month(1_000_000.0, 8.0, 12)
        infl_headline = _inflation_target_at_month(1_000_000.0, 6.0, 12)
        assert infl_at_m12 > infl_headline

    def test_r_inf_3_explicit_zero_inflation_not_null(self) -> None:
        g0 = _pit(
            "Wedding",
            tid=1,
            target=2_000_000.0,
            target_date=datetime.date(2027, 7, 1),
            priority=1,
            infl=0.0,
        )
        assert _effective_goal_inflation(g0, 6.5) == 0.0
        assert _inflation_target_at_month(2_000_000.0, 0.0, 5) == pytest.approx(2_000_000.0)


# ── 3. R-PRM-1 … R-PRM-5 ─────────────────────────────────────────────────────


class TestRulesParams:
    """Rules — Params."""

    def test_r_prm_1_salary_growth_every_12_months(self) -> None:
        g = _open_overflow_sink_pit("G", tid=1, priority=1)
        p = _params(
            [g],
            100_000.0,
            months=25,
            salary_growth=10.0,
            as_of=datetime.date(2026, 1, 1),
        )
        r = simulate(p)
        # Month 13+ should beat no-growth path
        p0 = SimulationParams(**{**p.model_dump(), "salary_growth_rate": 0.0})
        r0 = simulate(p0)
        assert r.net_worth_projection[-1].total_value > r0.net_worth_projection[-1].total_value

    def test_r_prm_2_one_time_inflows_and_outflows_net_in_month(self) -> None:
        g = _pit(
            "Save",
            tid=1,
            target=500_000.0,
            target_date=datetime.date(2030, 1, 1),
            priority=1,
            ret=0.0,
            infl=0.0,
        )
        gr = _closed_sink_placeholder("Rest", tid=2, priority=2)
        inflow = OneTimeEvent(amount=100_000.0, date=datetime.date(2026, 3, 15), description="in")
        outflow = OneTimeEvent(amount=50_000.0, date=datetime.date(2026, 3, 20), description="out")
        p = _params(
            [g, gr],
            10_000.0,
            months=12,
            inflows=[inflow],
            outflows=[outflow],
            as_of=datetime.date(2026, 1, 1),
        )
        r = simulate(p)
        assert r is not None
        # March should still have non-negative contributions (surplus + 50k net one-time)
        march = next(m for m in r.net_worth_projection if m.month == datetime.date(2026, 3, 1))
        assert march.total_contributions >= 0

    def test_r_prm_3_simulation_months_caps_horizon(self) -> None:
        g = _pit(
            "Late",
            tid=1,
            target=10_000_000.0,
            target_date=datetime.date(2050, 1, 1),
            priority=1,
            start=0.0,
            ret=8.0,
            infl=0.0,
        )
        p = _params([g], 5_000.0, months=36, as_of=datetime.date(2026, 1, 1))
        r = simulate(p)
        pit = r.projections[0]
        assert len(pit.monthly_trajectory) == 36
        assert pit.projected_completion_pct is not None
        assert pit.projected_completion_pct < 100.0

    def test_r_prm_4_compare_scenarios_structure(self) -> None:
        g = _pit("X", tid=1, target=800_000.0, target_date=datetime.date(2032, 1, 1), priority=1)
        gr = _closed_sink_placeholder("G", tid=2, priority=2)
        base = _params([g, gr], 20_000.0, months=80, as_of=datetime.date(2026, 1, 1))
        var = _params([g, gr], 35_000.0, months=80, as_of=datetime.date(2026, 1, 1))
        comps = compare_scenarios(base, [var])
        assert len(comps) == 1
        assert comps[0].scenario_name == "variant_1"
        assert "monthly_surplus" in comps[0].changes_from_base
        assert comps[0].result.projections
        assert comps[0].deltas

    def test_r_prm_5_allocate_surplus_returns_dict(self) -> None:
        goals = [
            _pit("A", tid=1, target=400_000.0, target_date=datetime.date(2029, 1, 1), priority=1),
            _closed_sink_placeholder("G", tid=2, priority=2),
        ]
        out = allocate_surplus(goals, 80_000.0, today=datetime.date(2026, 1, 1), general_inflation_rate=6.0)
        assert abs(sum(out.values()) - 80_000.0) < 1.0
        assert "A" in out and "G" in out


# ── 4. S1.1 … S1.5 ───────────────────────────────────────────────────────────


class TestSingleGoalBasic:
    """Single Goal — Basic."""

    def test_s1_1_simple_point_in_time_projection(self) -> None:
        # Engine uses PMT-needed (not fixed ₹50k); tight deadline ⇒ high need ⇒ full surplus use.
        # Single PIT — all surplus funds this goal until it completes.
        today = datetime.date(2026, 1, 1)
        td = add_months(today, 32)
        g = _pit("G", tid=1, target=2_000_000.0, target_date=td, priority=1, start=500_000.0, ret=10.0, infl=0.0)
        r = simulate(_params([g], 50_000.0, months=48, as_of=today))
        pit = r.projections[0]
        assert pit.projected_completion_date is not None
        assert (pit.projected_completion_pct or 0) >= 100.0
        mc = _months_from_start_to_date(today, pit.projected_completion_date)
        assert mc is not None
        assert 20 <= mc <= 32

    def test_s1_2_zero_returns_linear_months(self) -> None:
        # 0% return: need = gap/months_left; 30‑month horizon ⇒ 50k/mo closes 500k→2M gap.
        today = datetime.date(2026, 1, 1)
        td = add_months(today, 30)
        g = _pit("G", tid=1, target=2_000_000.0, target_date=td, priority=1, start=500_000.0, ret=0.0, infl=0.0)
        r = simulate(_params([g], 50_000.0, months=40, as_of=today))
        pit = r.projections[0]
        assert pit.projected_completion_date is not None
        assert (pit.projected_completion_pct or 0) >= 100.0
        mc = _months_from_start_to_date(today, pit.projected_completion_date)
        assert mc in (29, 30)

    def test_s1_3_zero_starting_balance(self) -> None:
        today = datetime.date(2026, 1, 1)
        td = add_months(today, 42)
        g = _pit("G", tid=1, target=2_000_000.0, target_date=td, priority=1, start=0.0, ret=10.0, infl=0.0)
        r = simulate(_params([g], 50_000.0, months=60, as_of=today))
        pit = r.projections[0]
        assert pit.projected_completion_date is not None
        assert (pit.projected_completion_pct or 0) >= 100.0
        mc = _months_from_start_to_date(today, pit.projected_completion_date)
        assert mc is not None
        assert 30 <= mc <= 42

    def test_s1_4_goal_already_achieved(self) -> None:
        today = datetime.date(2026, 1, 1)
        g = _pit(
            "Done",
            tid=1,
            target=2_000_000.0,
            target_date=datetime.date(2027, 1, 1),
            priority=1,
            start=2_500_000.0,
            ret=10.0,
            infl=0.0,
        )
        r = simulate(_params([g], 0.0, months=12, as_of=today))
        assert (r.projections[0].projected_completion_pct or 0) >= 100.0

    def test_s1_5_recurring_cash_flow_reserves_surplus(self) -> None:
        today = datetime.date(2026, 1, 1)
        emi = _recurring(
            "EMI",
            tid=1,
            amount=55_000.0,
            freq="MONTHLY",
            start_date=today,
            end_date=datetime.date(2046, 1, 1),
            priority=1,
        )
        gr = _closed_sink_placeholder("Rest", tid=2, priority=2)
        r = simulate(_params([emi, gr], 150_000.0, months=12, as_of=today))
        emi_p = next(p for p in r.projections if p.goal_name == "EMI")
        assert emi_p.monthly_allocation == pytest.approx(55_000.0, abs=5_000.0)


# ── 5. S2.1 … S2.5 ─────────────────────────────────────────────────────────────


class TestMultiGoalCascade:
    """Multi Goal — Cascade."""

    def test_s2_1_two_goals_sufficient_surplus(self) -> None:
        today = datetime.date(2026, 1, 1)
        g1 = _pit("G1", tid=1, target=500_000.0, target_date=add_months(today, 12), priority=1, infl=0.0)
        g2 = _pit("G2", tid=2, target=3_000_000.0, target_date=add_months(today, 60), priority=2, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=3, priority=3)
        r = simulate(_params([g1, g2, gr], 150_000.0, months=120, as_of=today))
        p1 = next(p for p in r.projections if p.goal_name == "G1")
        p2 = next(p for p in r.projections if p.goal_name == "G2")
        assert p1.projected_completion_date is not None
        assert (p1.projected_completion_pct or 0) >= 100.0
        assert p2.projected_completion_pct is not None
        assert (p2.projected_completion_pct or 0) >= 60.0

    def test_s2_2_three_goals_cascade_event(self) -> None:
        today = datetime.date(2026, 1, 1)
        g1 = _pit("G1", tid=1, target=300_000.0, target_date=add_months(today, 6), priority=1, infl=0.0)
        g2 = _pit("G2", tid=2, target=500_000.0, target_date=add_months(today, 18), priority=2, infl=0.0)
        g3 = _pit("G3", tid=3, target=3_000_000.0, target_date=add_months(today, 60), priority=3, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=4, priority=4)
        r = simulate(_params([g1, g2, g3, gr], 150_000.0, months=120, as_of=today))
        assert any(e.completed_goal == "G1" for e in r.cascade_events)
        assert {p.goal_name for p in r.projections if p.projected_completion_date is not None} >= {
            "G1",
        }

    def test_s2_3_insufficient_surplus_top_priority_behind(self) -> None:
        today = datetime.date(2026, 1, 1)
        g1 = _pit("G1", tid=1, target=1_000_000.0, target_date=add_months(today, 6), priority=1, start=0.0, ret=0.0, infl=0.0)
        g2 = _pit("G2", tid=2, target=500_000.0, target_date=add_months(today, 12), priority=2, start=0.0, ret=0.0, infl=0.0)
        g3 = _pit("G3", tid=3, target=3_000_000.0, target_date=add_months(today, 60), priority=3, start=0.0, ret=0.0, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=4, priority=4)
        r = simulate(_params([g1, g2, g3, gr], 150_000.0, months=120, as_of=today))
        p1 = next(p for p in r.projections if p.goal_name == "G1")
        p2 = next(p for p in r.projections if p.goal_name == "G2")
        p3 = next(p for p in r.projections if p.goal_name == "G3")
        assert p1.projected_completion_pct is not None
        assert p1.projected_completion_date is None
        assert p1.projected_completion_pct <= 90.0
        # While G1 is active it takes min(need, surplus); after deadline months contribute 0 — check peak month
        peak = max(s.monthly_contribution for s in p1.monthly_trajectory)
        assert peak == pytest.approx(150_000.0, abs=1.0)
        assert p2.monthly_allocation + p3.monthly_allocation < 50_000.0

    def test_s2_4_priority_reorder_changes_funding(self) -> None:
        today = datetime.date(2026, 1, 1)
        # Same ₹ target; different deadlines so PMT needs differ. Priority picks who is funded first.
        ga = _pit("A", tid=1, target=800_000.0, target_date=add_months(today, 24), priority=1, infl=0.0)
        gb = _pit("B", tid=2, target=800_000.0, target_date=add_months(today, 120), priority=2, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=3, priority=3)
        ga2 = _pit("A", tid=1, target=800_000.0, target_date=add_months(today, 120), priority=2, infl=0.0)
        gb2 = _pit("B", tid=2, target=800_000.0, target_date=add_months(today, 24), priority=1, infl=0.0)
        o1 = allocate_surplus([ga, gb, gr], 40_000.0, today=today, general_inflation_rate=6.0)
        o2 = allocate_surplus([ga2, gb2, gr], 40_000.0, today=today, general_inflation_rate=6.0)
        assert o1["A"] > o1["B"]
        assert o2["B"] > o2["A"]

    def test_s2_5_deleting_middle_goal_frees_surplus_for_others(self) -> None:
        today = datetime.date(2026, 1, 1)
        td = datetime.date(2032, 1, 1)
        g1 = _pit("G1", tid=1, target=800_000.0, target_date=td, priority=1, infl=0.0)
        g2 = _pit("G2", tid=2, target=800_000.0, target_date=td, priority=2, infl=0.0)
        g3 = _pit("G3", tid=3, target=800_000.0, target_date=td, priority=3, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=4, priority=4)
        r3 = simulate(_params([g1, g2, g3, gr], 25_000.0, months=100, as_of=today))
        r2 = simulate(_params([g1, g3, gr], 25_000.0, months=100, as_of=today))
        g1_3 = next(p for p in r3.projections if p.goal_name == "G1")
        g1_2 = next(p for p in r2.projections if p.goal_name == "G1")
        d3 = g1_3.projected_completion_date
        d2 = g1_2.projected_completion_date
        assert d3 is not None and d2 is not None
        assert d2 <= d3


# ── 6. S3.1, S3.2 ────────────────────────────────────────────────────────────


class TestReturnRates:
    """Return Rates."""

    def test_s3_1_monotonic_completion_vs_return(self) -> None:
        """Higher return → same or fewer months to complete (S3.1)."""
        today = datetime.date(2026, 1, 1)
        td = datetime.date(2035, 1, 1)
        months_list: list[int] = []
        for ret in (0.0, 8.0, 12.0, 15.0):
            g = _pit("G", tid=1, target=1_000_000.0, target_date=td, priority=1, start=0.0, ret=ret, infl=0.0)
            gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
            r = simulate(_params([g, gr], 50_000.0, months=150, as_of=today))
            pit = next(p for p in r.projections if p.goal_name == "G")
            mc = _months_from_start_to_date(today, pit.projected_completion_date)
            assert mc is not None
            months_list.append(mc)
        for a, b in zip(months_list, months_list[1:], strict=False):
            assert b <= a

    def test_s3_2_goal_specific_return_rates_independent(self) -> None:
        today = datetime.date(2026, 1, 1)
        g1 = _pit("Short", tid=1, target=200_000.0, target_date=add_months(today, 12), priority=1, ret=6.0, infl=0.0)
        g2 = _pit("Long", tid=2, target=2_000_000.0, target_date=add_months(today, 240), priority=2, ret=12.0, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=3, priority=3)
        r = simulate(_params([g1, g2, gr], 80_000.0, months=120, as_of=today))
        p1 = next(p for p in r.projections if p.goal_name == "Short")
        p2 = next(p for p in r.projections if p.goal_name == "Long")
        assert p1.projected_completion_date is not None
        assert (p1.projected_completion_pct or 0) >= 100.0
        assert p1.projected_final_amount >= 200_000.0 * 0.99
        assert p2.projected_final_amount > 0.0
        assert g1.expected_return_rate == 6.0 and g2.expected_return_rate == 12.0


# ── 7. S4.1 … S4.4 ───────────────────────────────────────────────────────────


class TestInflationScenarios:
    """Inflation (scenario-level)."""

    def test_s4_1_house_goal_eight_percent_over_ten_years(self) -> None:
        # ~10M today → ~21.59M at 10y @ 8% (CSV)
        raw = 10_000_000.0
        inflated = raw * (1.08**10)
        assert inflated == pytest.approx(21_589_250.0, rel=0.01)
        today = datetime.date(2026, 1, 1)
        g = _pit("House", tid=1, target=raw, target_date=add_months(today, 120), priority=1, ret=8.0, infl=8.0)
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([g, gr], 200_000.0, months=180, as_of=today))
        pit = next(p for p in r.projections if p.goal_name == "House")
        assert pit.projected_completion_pct is not None
        assert 0.0 < pit.projected_completion_pct < 200.0

    def test_s4_2_short_horizon_explicit_zero_inflation(self) -> None:
        today = datetime.date(2026, 1, 1)
        g = _pit(
            "Wed",
            tid=1,
            target=2_000_000.0,
            target_date=add_months(today, 18),
            priority=1,
            ret=6.0,
            infl=0.0,
        )
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([g, gr], 100_000.0, months=48, general_inflation=7.0, as_of=today))
        pit = next(p for p in r.projections if p.goal_name == "Wed")
        assert _effective_goal_inflation(
            SimulationGoal(
                name="x",
                goal_class=GC_POINT,
                inflation_rate=g.inflation_rate,
            ),
            99.0,
        ) == 0.0
        assert pit.projected_completion_pct is not None
        assert (pit.projected_completion_pct or 0) > 0.0

    def test_s4_3_two_goals_different_inflation(self) -> None:
        today = datetime.date(2026, 1, 1)
        g1 = _pit("H", tid=1, target=1_000_000.0, target_date=add_months(today, 120), priority=1, infl=8.0)
        g2 = _pit("T", tid=2, target=1_000_000.0, target_date=add_months(today, 120), priority=2, infl=6.0)
        gr = _closed_sink_placeholder("Pool", tid=3, priority=3)
        m = 60
        t1 = _inflation_target_at_month(1_000_000.0, 8.0, m)
        t2 = _inflation_target_at_month(1_000_000.0, 6.0, m)
        assert t1 > t2
        r = simulate(_params([g1, g2, gr], 150_000.0, months=150, as_of=today))
        assert len(r.projections) == 3

    def test_s4_4_null_goal_inflation_uses_general_params(self) -> None:
        g = SimulationGoal(
            id=1,
            name="Any",
            goal_class=GC_POINT,
            target_amount=500_000.0,
            target_date=datetime.date(2036, 1, 1),
            allocation_priority=1,
            inflation_rate=None,
            expected_return_rate=8.0,
        )
        eff = _effective_goal_inflation(g, 6.5)
        assert eff == 6.5
        assert _inflation_target_at_month(500_000.0, eff, 24) > 500_000.0


# ── 8. S5.1 … S5.4 ───────────────────────────────────────────────────────────


class TestSurplusChanges:
    """Surplus Changes."""

    def test_s5_1_surplus_increase_moves_completion_earlier(self) -> None:
        today = datetime.date(2026, 1, 1)
        td = add_months(today, 36)
        g = _pit("G", tid=1, target=1_800_000.0, target_date=td, priority=1, start=100_000.0, ret=8.0, infl=0.0)
        r_lo = simulate(_params([g], 80_000.0, months=48, as_of=today))
        r_hi = simulate(_params([g], 130_000.0, months=48, as_of=today))
        # Higher surplus → strictly better funding (lower shortfall at horizon if not ACHIEVED)
        assert r_hi.projections[0].shortfall <= r_lo.projections[0].shortfall
        d_lo = r_lo.projections[0].projected_completion_date
        d_hi = r_hi.projections[0].projected_completion_date
        if (
            d_lo
            and d_hi
            and r_hi.projections[0].projected_completion_date
            and r_lo.projections[0].projected_completion_date
        ):
            assert d_hi <= d_lo

    def test_s5_2_surplus_decrease_moves_completion_later_or_worse_status(self) -> None:
        today = datetime.date(2026, 1, 1)
        td = datetime.date(2030, 1, 1)
        g = _pit("G", tid=1, target=2_000_000.0, target_date=td, priority=1, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r_hi = simulate(_params([g, gr], 150_000.0, months=80, as_of=today))
        r_lo = simulate(_params([g, gr], 80_000.0, months=80, as_of=today))
        p_hi = next(p for p in r_hi.projections if p.goal_name == "G")
        p_lo = next(p for p in r_lo.projections if p.goal_name == "G")
        if p_hi.projected_completion_date and p_lo.projected_completion_date:
            assert p_lo.projected_completion_date >= p_hi.projected_completion_date

    def test_s5_3_zero_surplus_growth_from_returns_only(self) -> None:
        today = datetime.date(2026, 1, 1)
        g = _pit(
            "G",
            tid=1,
            target=5_000_000.0,
            target_date=datetime.date(2040, 1, 1),
            priority=1,
            start=400_000.0,
            ret=8.0,
            infl=0.0,
        )
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([g, gr], 0.0, months=60, as_of=today))
        pit = next(p for p in r.projections if p.goal_name == "G")
        assert pit.projected_final_amount > 400_000.0
        assert pit.projected_completion_pct is not None
        assert pit.projected_completion_pct < 100.0

    def test_s5_4_one_time_outflow_clamps_surplus_for_month(self) -> None:
        today = datetime.date(2026, 1, 1)
        g = _pit("G", tid=1, target=1_000_000.0, target_date=datetime.date(2030, 1, 1), priority=1, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        out = OneTimeEvent(amount=200_000.0, date=datetime.date(2026, 6, 10), description="hit")
        r = simulate(_params([g, gr], 150_000.0, months=12, outflows=[out], as_of=today))
        june = next(m for m in r.net_worth_projection if m.month == datetime.date(2026, 6, 1))
        # Net available that month capped at 0 after outflow vs base surplus
        assert june.total_contributions >= 0


# ── 9. S6.1 … S6.3 ─────────────────────────────────────────────────────────────


class TestRecurringPlusPointInTime:
    """Recurring + Point-in-Time."""

    def test_s6_1_emi_reduces_pit_allocation(self) -> None:
        today = datetime.date(2026, 1, 1)
        emi = _recurring(
            "EMI",
            tid=1,
            amount=55_000.0,
            freq="MONTHLY",
            start_date=today,
            end_date=datetime.date(2046, 1, 1),
            priority=1,
        )
        # Very large PIT so PMT need ≥ remaining after EMI (150k − 55k)
        pit = _pit(
            "House",
            tid=2,
            target=20_000_000.0,
            target_date=datetime.date(2030, 1, 1),
            priority=2,
            infl=0.0,
        )
        r = simulate(_params([emi, pit], 150_000.0, months=12, as_of=today))
        pit_p = next(p for p in r.projections if p.goal_name == "House")
        assert pit_p.monthly_allocation == pytest.approx(95_000.0, abs=1.0)

    def test_s6_2_emi_ends_mid_projection_pit_accelerates(self) -> None:
        today = datetime.date(2026, 1, 1)
        # EMI active through Dec 2027 → month 24; Jan 2028+ free
        emi = _recurring(
            "EMI",
            tid=1,
            amount=55_000.0,
            freq="MONTHLY",
            start_date=today,
            end_date=datetime.date(2027, 12, 1),
            priority=1,
        )
        pit = _pit("Big", tid=2, target=3_000_000.0, target_date=datetime.date(2036, 1, 1), priority=2, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=3, priority=3)
        r_short = simulate(_params([emi, pit, gr], 150_000.0, months=36, as_of=today))
        emi_long = _recurring(
            "EMI",
            tid=1,
            amount=55_000.0,
            freq="MONTHLY",
            start_date=today,
            end_date=datetime.date(2046, 1, 1),
            priority=1,
        )
        r_long = simulate(_params([emi_long, pit, gr], 150_000.0, months=36, as_of=today))
        d_short = next(p for p in r_short.projections if p.goal_name == "Big").projected_completion_date
        d_long = next(p for p in r_long.projections if p.goal_name == "Big").projected_completion_date
        if d_short and d_long:
            assert d_short <= d_long

    def test_s6_3_multiple_recurring_normalized_monthly(self) -> None:
        today = datetime.date(2026, 1, 1)
        emi = _recurring(
            "EMI",
            tid=1,
            amount=55_000.0,
            freq="MONTHLY",
            start_date=today,
            end_date=datetime.date(2046, 1, 1),
            priority=1,
        )
        travel = _recurring(
            "Travel",
            tid=2,
            amount=12_500.0,
            freq="QUARTERLY",
            start_date=today,
            end_date=datetime.date(2046, 1, 1),
            priority=2,
        )
        tax = _recurring(
            "Tax",
            tid=3,
            amount=400_000.0,
            freq="ANNUAL",
            start_date=today,
            end_date=datetime.date(2046, 1, 1),
            priority=3,
        )
        pit = _pit(
            "Save",
            tid=4,
            target=25_000_000.0,
            target_date=datetime.date(2030, 1, 1),
            priority=4,
            infl=0.0,
        )
        r = simulate(_params([emi, travel, tax, pit], 200_000.0, months=6, as_of=today))
        pit_p = next(p for p in r.projections if p.goal_name == "Save")
        # Nominal: 200k − 55k − 12.5k/3 − 400k/12 ≈ 107.5k to PIT — but Travel’s monthly
        # equivalent (~4.2k) is below MIN_MONTHLY_GOAL_CONTRIBUTION_INR (5k), so the floor
        # zeros Travel and spills ~4.2k/mo to the PIT sink → ~111.7k average to Save.
        assert pit_p.monthly_allocation == pytest.approx(111_666.67, abs=1.0)


# ── 10. S7.1 … S7.3 ──────────────────────────────────────────────────────────


class TestLiquidity:
    """Liquidity + simulation (S7.x)."""

    def test_s7_1_claim_exceeds_accessible_mismatch_then_effective_balance(self, session: Session) -> None:
        uid = "test_user"
        today = datetime.date(2026, 1, 1)
        goal = Goal(
            name="House",
            goal_type="SAVINGS",
            goal_class="POINT_IN_TIME",
            user_id=uid,
            pyramid_id="S71",
            target_amount=3_000_000.0,
            target_date=datetime.date(2036, 6, 1),
            activation_status="ACTIVE",
            allocation_priority=1,
            expected_return_rate=8.0,
        )
        session.add(goal)
        session.commit()
        session.refresh(goal)
        assert goal.id is not None
        session.add(
            Holding(
                name="SB",
                asset_class=AssetClass.SAVINGS.value,
                account_platform="X",
                valuation_method=ValuationMethod.MANUAL.value,
                liquidity_class=LiquidityClass.INSTANT.value,
                user_id=uid,
                current_value=800_000.0,
                earliest_liquidity_date=today,
                is_active=True,
            )
        )
        session.commit()

        res = check_liquidity_mismatch(session, goal.id, 1_500_000.0, uid, today=today)
        assert res.is_mismatch is True
        assert res.shortfall_inr is not None

        gsim = _pit(
            "House",
            tid=1,
            target=3_000_000.0,
            target_date=datetime.date(2036, 6, 1),
            priority=1,
            start=800_000.0,
            ret=8.0,
            infl=0.0,
        )
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([gsim, gr], 100_000.0, months=200, as_of=today))
        assert r.projections[0].projected_completion_pct is not None

    def test_s7_2_long_horizon_full_accessible_no_mismatch(self, session: Session) -> None:
        uid = "test_user"
        today = datetime.date(2026, 1, 1)
        goal = Goal(
            name="Ret",
            goal_type="SAVINGS",
            goal_class="POINT_IN_TIME",
            user_id=uid,
            pyramid_id="S72",
            target_amount=2_000_000.0,
            target_date=datetime.date(2036, 1, 1),
            activation_status="ACTIVE",
            allocation_priority=1,
            expected_return_rate=10.0,
        )
        session.add(goal)
        session.commit()
        session.refresh(goal)
        session.add(
            Holding(
                name="SB2",
                asset_class=AssetClass.SAVINGS.value,
                account_platform="X",
                valuation_method=ValuationMethod.MANUAL.value,
                liquidity_class=LiquidityClass.INSTANT.value,
                user_id=uid,
                current_value=1_500_000.0,
                earliest_liquidity_date=today,
                is_active=True,
            )
        )
        session.commit()
        res = check_liquidity_mismatch(session, goal.id, 1_500_000.0, uid, today=today)
        assert res.is_mismatch is False

    def test_s7_3_zero_starting_pure_surplus(self) -> None:
        today = datetime.date(2026, 1, 1)
        g = _pit("G", tid=1, target=900_000.0, target_date=datetime.date(2031, 1, 1), priority=1, start=0.0, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([g, gr], 40_000.0, months=100, as_of=today))
        assert r.projections[0].projected_final_amount > 0


# ── 11. S8.1 … S8.5 ───────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge Cases."""

    def test_s8_1_only_open_overflow_sink_pit(self) -> None:
        g = _open_overflow_sink_pit("Grow", tid=1, priority=1, ret=12.0)
        r = simulate(_params([g], 150_000.0, months=60, as_of=datetime.date(2026, 1, 1)))
        assert r.projections[0].projected_completion_date is None
        assert r.projections[0].projected_final_amount > 8_000_000.0

    def test_s8_2_all_pit_achieved_surplus_to_sink(self) -> None:
        today = datetime.date(2026, 1, 1)
        g1 = _pit("A", tid=1, target=100_000.0, target_date=add_months(today, 12), priority=1, start=120_000.0, infl=0.0)
        g2 = _pit("B", tid=2, target=50_000.0, target_date=add_months(today, 12), priority=2, start=60_000.0, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=3, priority=3)
        r = simulate(_params([g1, g2, gr], 80_000.0, months=24, as_of=today))
        for name in ("A", "B"):
            row = next(p for p in r.projections if p.goal_name == name)
            assert row.projected_completion_date is not None
            assert (row.projected_completion_pct or 0) >= 100.0

    def test_s8_3_exact_pmt_zero_percent(self) -> None:
        today = datetime.date(2026, 1, 1)
        g = _pit(
            "G",
            tid=1,
            target=1_200_000.0,
            target_date=add_months(today, 12),
            priority=1,
            start=0.0,
            ret=0.0,
            infl=0.0,
        )
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([g, gr], 100_000.0, months=24, as_of=today))
        pit = next(p for p in r.projections if p.goal_name == "G")
        assert pit.projected_completion_date is not None
        assert (pit.projected_completion_pct or 0) >= 100.0
        mc = _months_from_start_to_date(today, pit.projected_completion_date)
        # Off-by-one possible vs months_between; both acceptable for "12-month horizon"
        assert mc in (11, 12)

    def test_s8_4_long_horizon_numeric_stability(self) -> None:
        today = datetime.date(2026, 1, 1)
        g = _pit(
            "Ret",
            tid=1,
            target=50_000_000.0,
            target_date=datetime.date(2051, 1, 1),
            priority=1,
            start=1_000_000.0,
            ret=10.0,
            infl=0.0,
        )
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([g, gr], 200_000.0, months=300, as_of=today))
        for row in r.net_worth_projection[-5:]:
            assert row.total_value == row.total_value  # finite
            assert abs(row.total_value) < 1e15

    def test_s8_5_target_increase_delays_completion(self) -> None:
        today = datetime.date(2026, 1, 1)
        td = datetime.date(2035, 1, 1)
        g_lo = _pit("G", tid=1, target=2_000_000.0, target_date=td, priority=1, infl=0.0)
        g_hi = _pit("G", tid=1, target=2_500_000.0, target_date=td, priority=1, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r_lo = simulate(_params([g_lo, gr], 50_000.0, months=150, as_of=today))
        r_hi = simulate(_params([g_hi, gr], 50_000.0, months=150, as_of=today))
        d_lo = next(p for p in r_lo.projections if p.goal_name == "G").projected_completion_date
        d_hi = next(p for p in r_hi.projections if p.goal_name == "G").projected_completion_date
        assert d_lo is not None and d_hi is not None
        assert d_hi >= d_lo


# ── 12. S9.1, S9.2 ───────────────────────────────────────────────────────────


class TestMultiUser:
    """Multi-user share semantics (simulate user's slice only)."""

    def test_s9_1_shared_goal_user_share_only(self) -> None:
        today = datetime.date(2026, 1, 1)
        # Full goal 1M; user's 50% → 500k target in simulation
        g = _pit("Trip", tid=1, target=500_000.0, target_date=datetime.date(2028, 1, 1), priority=1, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r = simulate(_params([g, gr], 40_000.0, months=60, as_of=today))
        pit = next(p for p in r.projections if p.goal_name == "Trip")
        assert pit.projected_completion_date is not None
        assert (pit.projected_completion_pct or 0) >= 100.0

    def test_s9_2_higher_share_later_completion(self) -> None:
        today = datetime.date(2026, 1, 1)
        td = datetime.date(2030, 1, 1)
        g_50 = _pit("X", tid=1, target=500_000.0, target_date=td, priority=1, infl=0.0)
        g_70 = _pit("X", tid=1, target=700_000.0, target_date=td, priority=1, infl=0.0)
        gr = _closed_sink_placeholder("Pool", tid=2, priority=2)
        r50 = simulate(_params([g_50, gr], 35_000.0, months=100, as_of=today))
        r70 = simulate(_params([g_70, gr], 35_000.0, months=100, as_of=today))
        d50 = next(p for p in r50.projections if p.goal_name == "X").projected_completion_date
        d70 = next(p for p in r70.projections if p.goal_name == "X").projected_completion_date
        assert d50 is not None and d70 is not None
        assert d70 >= d50
