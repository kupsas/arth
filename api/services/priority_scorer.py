"""
Priority scoring for goals (Sub-Plan E).

Computes a 0–100 composite score from four weighted dimensions so the system can
suggest funding order. See ``docs/personal-data/goals-architecture-master-plan.md``
§ Sub-Plan E.

Weights:
  - Time pressure: 35%
  - Consequence severity: 30%
  - Funding feasibility (inverted — at-risk goals score higher): 25%
  - Asset alignment (inverted — more covered by maturing assets scores lower): 10%
"""

from __future__ import annotations

import calendar
import datetime
import logging
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api.models import Goal
from api.services.liquidity_service import match_holdings_to_goal
from api.services.surplus_calculator import compute_surplus

logger = logging.getLogger(__name__)

# ── Weights (must sum to 1.0) ─────────────────────────────────────────────
W_TIME = 0.35
W_CONSEQUENCE = 0.30
W_FEASIBILITY = 0.25
W_ASSET = 0.10

# Consequence lookup by goal_subtype (0–100).
CONSEQUENCE_SCORES: dict[str, float] = {
    "LOAN_PAYOFF": 95.0,
    "EMERGENCY_FUND": 85.0,
    "HOME_PURCHASE": 70.0,
    "WEDDING": 65.0,
    "CHILD_EDUCATION": 60.0,
    "RETIREMENT": 55.0,
    "VEHICLE": 40.0,
    "TRAVEL": 20.0,
    "CUSTOM": 30.0,
}


class PriorityBreakdown(BaseModel):
    """Raw 0–100 sub-scores before weighting."""

    time_pressure: float = Field(ge=0, le=100)
    consequence_severity: float = Field(ge=0, le=100)
    feasibility_urgency: float = Field(ge=0, le=100)
    asset_alignment: float = Field(ge=0, le=100)


class GoalPriority(BaseModel):
    """One goal's priority result."""

    goal_id: int
    goal_name: str
    priority_score: float
    suggested_rank: int
    breakdown: PriorityBreakdown
    explanation: str
    needs_revision: bool


class PriorityResult(BaseModel):
    """Full scoring run for a user."""

    user_id: str
    priorities: list[GoalPriority]
    monthly_surplus: float
    active_goal_count: int
    computed_at: datetime.datetime = Field(
        description="UTC timestamp when scores were computed.",
    )


def _effective_goal_class(goal: Goal) -> str:
    """Resolve goal_class; infer from goal_type when unset (legacy rows)."""
    gc = (goal.goal_class or "").strip().upper()
    if gc in ("POINT_IN_TIME", "RECURRING_CASH_FLOW", "GROWTH"):
        return gc
    # Legacy inference
    if goal.goal_type == "INVESTMENT":
        return "GROWTH"
    if goal.goal_type == "EXPENSE_LIMIT":
        return "RECURRING_CASH_FLOW"
    return "POINT_IN_TIME"


def _months_between(start: datetime.date, end: datetime.date) -> int:
    """Whole calendar months from *start* to *end* (non-negative)."""
    if end < start:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month)


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(d: datetime.date, months: int) -> datetime.date:
    """Add *months* to *d*, clamping the day to the target month's last day."""
    m0 = d.month - 1 + months
    year = d.year + m0 // 12
    month = m0 % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return datetime.date(year, month, day)


def _next_recurrence_date(goal: Goal, today: datetime.date) -> datetime.date | None:
    """
    Next occurrence on or after *today* for a recurring cash-flow goal.

    If ``recurrence_start`` is missing, returns None (caller treats as low urgency).
    """
    if goal.recurrence_start is None:
        return None
    freq = (goal.recurrence_frequency or "MONTHLY").strip().upper()
    step = {"MONTHLY": 1, "QUARTERLY": 3, "ANNUAL": 12}.get(freq, 1)

    cur = goal.recurrence_start
    # If recurrence ended before today, no upcoming payment in window
    if goal.recurrence_end is not None and goal.recurrence_end < today:
        return None

    guard = 0
    while cur < today and guard < 600:
        cur = _add_months(cur, step)
        guard += 1
        if goal.recurrence_end is not None and cur > goal.recurrence_end:
            return None

    if goal.recurrence_end is not None and cur > goal.recurrence_end:
        return None
    return cur


def time_pressure(goal: Goal, today: datetime.date) -> float:
    """
    Dimension 1: how urgently this goal needs surplus funding (0–100).

    GROWTH goals: always low (10). RECURRING: urgency from proximity of next payment.
    POINT_IN_TIME: ``behind_ratio`` vs timeline (see master plan).
    """
    gclass = _effective_goal_class(goal)

    if gclass == "GROWTH":
        return 10.0

    if gclass == "RECURRING_CASH_FLOW":
        nxt = _next_recurrence_date(goal, today)
        if nxt is None:
            return 0.0
        months_to_next = _months_between(today, nxt)
        return max(0.0, 100.0 - months_to_next * 20.0)

    # POINT_IN_TIME (or legacy treated as such)
    target = goal.target_amount
    if target is None or target <= 0:
        return 0.0

    saved = goal.starting_balance if goal.starting_balance is not None else (goal.current_value or 0.0)
    if saved >= target:
        return 0.0

    remaining = target - saved
    funding_gap_ratio = max(0.0, min(1.0, remaining / target))

    if goal.target_date is None:
        return 10.0

    if goal.target_date <= today:
        return 100.0

    months_remaining = _months_between(today, goal.target_date)
    timeline_start = goal.created_at.date() if goal.created_at else today
    total_months = _months_between(timeline_start, goal.target_date)
    total_months = max(total_months, 1)

    time_remaining_ratio = max(0.0, min(1.0, months_remaining / float(total_months)))

    if time_remaining_ratio == 0.0:
        return 100.0

    behind_ratio = funding_gap_ratio / time_remaining_ratio
    return min(100.0, behind_ratio * 50.0)


def consequence_severity(goal: Goal) -> float:
    """Dimension 2: impact if the goal is missed (0–100)."""
    gclass = _effective_goal_class(goal)
    if gclass == "GROWTH":
        return 10.0

    subtype = (goal.goal_subtype or "").strip().upper() or None
    base = CONSEQUENCE_SCORES.get(subtype, 30.0) if subtype else 30.0

    # Contractual recurring obligations (EMI-like): bump to high consequence.
    if gclass == "RECURRING_CASH_FLOW":
        freq = (goal.recurrence_frequency or "").strip().upper()
        amt = goal.recurrence_amount or 0.0
        if freq == "MONTHLY" and amt > 0:
            return max(base, 95.0)

    return base


def _projected_corpus(
    balance: float,
    monthly_allocation: float,
    months: int,
    annual_return_pct: float,
) -> float:
    """FV of starting balance + monthly contributions at monthly rate *months*."""
    r = (annual_return_pct / 12.0) / 100.0
    n = max(0, months)
    if n == 0:
        return balance
    if r > 1e-12:
        growth = (1.0 + r) ** n
        return balance * growth + monthly_allocation * (growth - 1.0) / r
    return balance + monthly_allocation * n


def funding_feasibility(
    goal: Goal,
    monthly_surplus: float,
    monthly_allocation_estimate: float,
    months_remaining: int,
) -> tuple[float, bool]:
    """
    Dimension 3: inverted — less feasible trajectory => higher score (needs more surplus).

    Returns (score 0–100, needs_revision).
    """
    gclass = _effective_goal_class(goal)
    needs_revision = False

    if gclass == "GROWTH":
        return 20.0, False

    if monthly_surplus <= 0 and gclass in ("POINT_IN_TIME", "RECURRING_CASH_FLOW"):
        # Nothing can be funded from surplus; flag high urgency on feasibility axis.
        return 90.0, False

    if gclass == "RECURRING_CASH_FLOW":
        rec = goal.recurrence_amount or 0.0
        half = monthly_surplus * 0.5
        if rec <= half:
            return 30.0, False
        # Tight or over budget vs surplus — scale 30–90
        ratio = rec / max(monthly_surplus, 1e-9)
        score = min(90.0, 30.0 + (ratio - 0.5) * 80.0)
        return score, False

    # POINT_IN_TIME
    target = goal.target_amount
    if target is None or target <= 0:
        return 20.0, False

    ann = goal.expected_return_rate if goal.expected_return_rate is not None else 10.0
    balance = goal.starting_balance if goal.starting_balance is not None else (goal.current_value or 0.0)

    projected = _projected_corpus(
        balance,
        monthly_allocation_estimate,
        months_remaining,
        ann,
    )
    feasibility_ratio = projected / target if target > 0 else 0.0

    if feasibility_ratio >= 1.0:
        return 30.0, False
    if feasibility_ratio >= 0.8:
        return 50.0, False
    if feasibility_ratio >= 0.5:
        return 80.0, False
    if feasibility_ratio >= 0.2:
        return 90.0, False
    needs_revision = True
    return 60.0, needs_revision


def asset_alignment(
    session: Session,
    goal: Goal,
    user_id: str,
    today: datetime.date,
) -> float:
    """
    Dimension 4: inverted — higher coverage by accessible holdings => lower score.

    GROWTH / no deadline: neutral 50 (whole portfolio matches per liquidity service).
    """
    gclass = _effective_goal_class(goal)
    if gclass == "GROWTH" or goal.target_date is None:
        return 50.0

    assert goal.id is not None
    try:
        matched = match_holdings_to_goal(session, goal.id, user_id, today=today)
    except ValueError:
        logger.warning("match_holdings_to_goal failed for goal_id=%s — neutral alignment", goal.id)
        return 50.0

    target = goal.target_amount or 0.0
    if target <= 0:
        return 50.0

    coverage_ratio = min(1.0, max(0.0, matched.total_accessible_value_inr / target))
    return max(0.0, 100.0 - coverage_ratio * 100.0)


def _dominant_factor_name(
    breakdown: PriorityBreakdown,
) -> Literal["time_pressure", "consequence", "feasibility", "asset_alignment"]:
    """Which dimension contributes the largest *weighted* slice."""
    weighted = {
        "time_pressure": W_TIME * breakdown.time_pressure,
        "consequence": W_CONSEQUENCE * breakdown.consequence_severity,
        "feasibility": W_FEASIBILITY * breakdown.feasibility_urgency,
        "asset_alignment": W_ASSET * breakdown.asset_alignment,
    }
    return max(weighted, key=weighted.get)  # type: ignore[arg-type]


def generate_explanation(
    goal: Goal,
    rank: int,
    breakdown: PriorityBreakdown,
    needs_revision: bool,
) -> str:
    """One-line explanation; dominant factor drives the blurb."""
    name = goal.name
    dominant = _dominant_factor_name(breakdown)

    if _effective_goal_class(goal) == "GROWTH":
        return (
            f"Ranked #{rank}: {name} — growth goal; absorbs remaining surplus "
            f"after higher-priority goals."
        )

    parts: list[str] = []
    if dominant == "time_pressure":
        parts.append("strong time pressure vs funding progress")
    elif dominant == "consequence":
        parts.append("high consequence if missed")
    elif dominant == "feasibility":
        parts.append("funding trajectory needs attention")
    else:
        parts.append("limited asset coverage before the target date")

    if needs_revision:
        parts.append("goal may be unrealistic at current savings — consider revising")

    suffix = "; ".join(parts)
    return f"Ranked #{rank}: {name} — {suffix}."


def compute_priority_scores(
    session: Session,
    user_id: str = "sashank",
    *,
    persist: bool = True,
    today: datetime.date | None = None,
) -> PriorityResult:
    """
    Score all **ACTIVE** goals for *user_id*, sort by composite score, assign ranks.

    Optionally persists ``Goal.system_priority_score`` (does not change
    ``allocation_priority`` — user reorder is separate).
    """
    uid = user_id.strip() or "sashank"
    as_of = today or datetime.datetime.now(datetime.UTC).date()

    goals = list(
        session.exec(
            select(Goal)
            .where(Goal.user_id == uid)
            .where(Goal.activation_status == "ACTIVE")
            .order_by(col(Goal.id))
        ).all()
    )

    surplus_result = compute_surplus(session, uid, months=6)
    monthly_surplus = surplus_result.monthly_surplus
    n_goals = len(goals)
    equal_share = monthly_surplus / max(n_goals, 1)

    rows: list[tuple[Goal, PriorityBreakdown, float, bool]] = []

    for g in goals:
        assert g.id is not None
        gclass = _effective_goal_class(g)

        months_left = 0
        if gclass == "POINT_IN_TIME" and g.target_date and g.target_date > as_of:
            months_left = _months_between(as_of, g.target_date)
        elif gclass == "POINT_IN_TIME" and g.target_date and g.target_date <= as_of:
            months_left = 0

        alloc_est = equal_share
        if g.monthly_allocation is not None and g.monthly_allocation > 0:
            alloc_est = g.monthly_allocation

        tp = time_pressure(g, as_of)
        cs = consequence_severity(g)
        feas, needs_rev = funding_feasibility(g, monthly_surplus, alloc_est, months_left)
        aa = asset_alignment(session, g, uid, as_of)

        composite = (
            W_TIME * tp
            + W_CONSEQUENCE * cs
            + W_FEASIBILITY * feas
            + W_ASSET * aa
        )

        bd = PriorityBreakdown(
            time_pressure=round(tp, 2),
            consequence_severity=round(cs, 2),
            feasibility_urgency=round(feas, 2),
            asset_alignment=round(aa, 2),
        )
        rows.append((g, bd, composite, needs_rev))

    # Sort: score desc, then expected_return_rate desc (tie-break for GROWTH), then id
    def sort_key(item: tuple[Goal, PriorityBreakdown, float, bool]) -> tuple[float, float, int]:
        goal, _, score, _ = item
        er = goal.expected_return_rate or 0.0
        return (-score, -er, -(goal.id or 0))

    rows.sort(key=sort_key)

    computed_at = datetime.datetime.now(datetime.UTC)
    priorities: list[GoalPriority] = []

    for rank, (g, bd, score, needs_rev) in enumerate(rows, start=1):
        expl = generate_explanation(g, rank, bd, needs_rev)
        priorities.append(
            GoalPriority(
                goal_id=g.id,  # type: ignore[arg-type]
                goal_name=g.name,
                priority_score=round(score, 2),
                suggested_rank=rank,
                breakdown=bd,
                explanation=expl,
                needs_revision=needs_rev,
            )
        )

        if persist:
            g.system_priority_score = round(score, 4)
            g.updated_at = computed_at
            session.add(g)

    if persist:
        session.commit()

    return PriorityResult(
        user_id=uid,
        priorities=priorities,
        monthly_surplus=round(monthly_surplus, 2),
        active_goal_count=n_goals,
        computed_at=computed_at,
    )


# Public aliases (plan / tests may reference underscore names)
_time_pressure = time_pressure
_consequence_severity = consequence_severity
_funding_feasibility = funding_feasibility
_asset_alignment = asset_alignment
_generate_explanation = generate_explanation
_next_recurrence_date = _next_recurrence_date
