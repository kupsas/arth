"""
Goal decomposition and pattern-based suggestions (Goals architecture Sub-Plan D).

Pure math helpers build sub-goal *specs* (Pydantic models). The API can preview them
or persist child ``Goal`` rows (``parent_goal_id`` points at the decomposed parent).

Design notes:
  - Point-in-time math follows the master plan: inflation-adjust target, FV of
    starting balance, remaining gap, then PMT (annuity payment) for monthly need.
  - ``today`` is injectable so tests stay deterministic (patch-free unit tests).
  - RecurringPattern rules mirror docs/personal-data/goals-architecture-master-plan.md.
"""

from __future__ import annotations

import calendar
import datetime
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api.models import Goal, RecurringPattern

logger = logging.getLogger(__name__)

# ── Counterparty categories (exact strings from pipeline / classifier) ────────

_CAT_FINANCIAL_SERVICES = "Financial Services, Insurance & Banking"
_CAT_FEES = "Fees, Charges & Interest"
_CAT_ASSET_MARKETS = "Asset Markets"
_CAT_TRAVEL = "Travel & Stay"

# Baseline expenses — not offered as “goals” (user spends, not saving targets).
_SKIP_GOAL_CATEGORIES = frozenset(
    {
        "Rent & Housing",
        "Utilities & Internet",
        "Mobile, OTT & Subscriptions",
        "Transport & Fuel",
        "Food & Dining",
        "Swiggy",
    }
)

Confidence = Literal["HIGH", "MEDIUM"]


class LoanParams(BaseModel):
    """Inputs for splitting a purchase into down payment + loan EMI."""

    total_cost: float = Field(..., gt=0, description="Full purchase price (INR)")
    down_payment_pct: float = Field(..., ge=0, le=1, description="Fraction e.g. 0.20")
    loan_interest_rate: float = Field(..., ge=0, le=50, description="Nominal annual %")
    loan_tenure_years: int = Field(..., ge=1, le=40)


class SubGoalSpec(BaseModel):
    """Suggested child goal — maps to a ``Goal`` row (with ``parent_goal_id``) on persist."""

    name: str
    tier: str = Field(..., description="L2 | L3 | L4")
    goal_type: str
    goal_class: str
    target_amount: float | None = None
    target_date: datetime.date | None = None
    recurrence_amount: float | None = None
    recurrence_frequency: str | None = None
    recurrence_start: datetime.date | None = None
    recurrence_end: datetime.date | None = None
    goal_subtype: str | None = None
    expected_return_rate: float | None = None
    starting_balance: float | None = None
    notes: str | None = None


class Milestone(BaseModel):
    """Year index (1 = first year from *today*) and corpus at end of that year."""

    year_index: int = Field(..., ge=1)
    target_date: datetime.date
    target_corpus: float


class RealityStatus(BaseModel):
    """Surplus vs required monthly contribution."""

    status: Literal["COMFORTABLE", "FEASIBLE", "GAP"]
    monthly_surplus: float
    monthly_required: float
    gap: float
    message: str


class DecompositionResult(BaseModel):
    """Output of point-in-time or debt decomposition."""

    parent_goal_id: int | None
    mode: Literal["POINT_IN_TIME", "DEBT"]
    sub_goals: list[SubGoalSpec]
    monthly_required: float
    inflation_adjusted_target: float | None = None
    reality_check: RealityStatus | None = None
    milestones: list[Milestone] = Field(default_factory=list)


class GoalSuggestion(BaseModel):
    """Suggested new goal inferred from a recurring transaction pattern."""

    source_pattern_id: int
    counterparty: str
    suggested_name: str
    goal_class: str
    goal_type: str
    recurrence_amount: float | None = None
    recurrence_frequency: str | None = None
    goal_subtype: str | None = None
    confidence: Confidence
    message: str


# ── Date helpers (no dateutil dependency) ─────────────────────────────────────


def add_months(d: datetime.date, months: int) -> datetime.date:
    """Add calendar months, clamping day for shorter months."""
    if months == 0:
        return d
    m0 = d.month - 1 + months
    y = d.year + m0 // 12
    m = m0 % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, day)


def add_calendar_years(d: datetime.date, years: int) -> datetime.date:
    """Add whole years; Feb 29 → Feb 28 in non-leap years."""
    y = d.year + years
    m, day = d.month, d.day
    max_d = calendar.monthrange(y, m)[1]
    return datetime.date(y, m, min(day, max_d))


def months_between(start: datetime.date, end: datetime.date) -> int:
    """Whole months from *start* (inclusive) to *end* (exclusive-ish).

    We use (y2-y1)*12 + (m2-m1) and adjust if end's day < start's day.
    """
    if end <= start:
        return 0
    y1, m1, d1 = start.year, start.month, start.day
    y2, m2, d2 = end.year, end.month, end.day
    total = (y2 - y1) * 12 + (m2 - m1)
    if d2 < d1:
        total -= 1
    return max(total, 0)


def _monthly_rate_from_annual(annual_pct: float) -> float:
    return annual_pct / 12.0 / 100.0


def _format_inr(x: float) -> str:
    if x >= 10000000:
        return f"₹{x/10000000:.2f} Cr"
    if x >= 100000:
        return f"₹{x/100000:.2f} L"
    return f"₹{x:,.0f}"


def _reality_check_status(monthly_required: float, surplus: float) -> RealityStatus:
    gap = max(0.0, monthly_required - surplus)
    if monthly_required <= surplus * 0.5:
        status: Literal["COMFORTABLE", "FEASIBLE", "GAP"] = "COMFORTABLE"
        msg = (
            f"Required {_format_inr(monthly_required)}/mo vs surplus {_format_inr(surplus)}/mo — comfortable headroom."
        )
    elif monthly_required <= surplus + 1e-6:
        status = "FEASIBLE"
        msg = (
            f"Required {_format_inr(monthly_required)}/mo fits within surplus "
            f"{_format_inr(surplus)}/mo (tight)."
        )
    else:
        status = "GAP"
        msg = (
            f"You need {_format_inr(monthly_required)}/mo but your surplus is "
            f"{_format_inr(surplus)}/mo. Gap: {_format_inr(gap)}/mo."
        )
    return RealityStatus(
        status=status,
        monthly_surplus=surplus,
        monthly_required=monthly_required,
        gap=gap,
        message=msg,
    )


def decompose_point_in_time_goal(
    goal: Goal,
    surplus: float,
    *,
    today: datetime.date | None = None,
    general_cpi: float = 6.0,
    inflation_horizon_years: float = 2.0,
) -> DecompositionResult:
    """Compute PMT, milestones, and sub-goal specs for a POINT_IN_TIME style goal.

    ``goal.target_amount`` and ``goal.target_date`` must be set; ``surplus`` is the
    headline monthly surplus from :func:`api.services.surplus_calculator.compute_surplus`.

    ``general_cpi`` is the annual % used when ``goal.goal_specific_inflation_rate`` is
    unset. API routes pass :func:`api.services.inflation_service.get_goal_inflation_rate`
    (EMA of IMF monthly CPI YoY). The default ``6.0`` keeps unit tests and offline
    scripts stable without a database.
    """
    if goal.target_amount is None or goal.target_date is None:
        raise ValueError("target_amount and target_date are required for decomposition")

    td = today or datetime.date.today()
    n = months_between(td, goal.target_date)
    if n <= 0:
        raise ValueError("target_date must be after today for decomposition")

    raw_target = float(goal.target_amount)
    pv = float(goal.starting_balance or 0.0)
    annual_inflation = float(goal.goal_specific_inflation_rate or general_cpi)
    annual_return = float(goal.expected_return_rate or 0.0)
    r = _monthly_rate_from_annual(annual_return)

    years_to_goal = n / 12.0
    if years_to_goal > inflation_horizon_years:
        inflation_adjusted_target = raw_target * (1.0 + annual_inflation / 100.0) ** years_to_goal
    else:
        inflation_adjusted_target = raw_target

    fv_start = pv * (1.0 + r) ** n if r > 0 else pv
    remaining = inflation_adjusted_target - fv_start
    if remaining <= 0:
        return DecompositionResult(
            parent_goal_id=goal.id,
            mode="POINT_IN_TIME",
            sub_goals=[],
            monthly_required=0.0,
            inflation_adjusted_target=inflation_adjusted_target,
            reality_check=_reality_check_status(0.0, surplus),
            milestones=[],
        )

    if r > 0:
        denom = (1.0 + r) ** n - 1.0
        monthly_required = remaining * r / denom if denom > 0 else remaining / n
    else:
        monthly_required = remaining / n

    milestones: list[Milestone] = []
    full_years = int(years_to_goal)
    for y in range(1, full_years + 1):
        m = 12 * y
        if m > n:
            break
        checkpoint = add_months(td, m)
        if checkpoint > goal.target_date:
            checkpoint = goal.target_date
        if r > 0:
            corpus = pv * (1.0 + r) ** m + monthly_required * ((1.0 + r) ** m - 1.0) / r
        else:
            corpus = pv + monthly_required * m
        milestones.append(
            Milestone(year_index=y, target_date=checkpoint, target_corpus=corpus)
        )

    sub_goals: list[SubGoalSpec] = []

    # L4 — recurring “save this much per month” toward the parent deadline.
    sub_goals.append(
        SubGoalSpec(
            name=f"{goal.name} — Monthly contribution",
            tier="L4",
            goal_type="SAVINGS",
            goal_class="RECURRING_CASH_FLOW",
            recurrence_amount=monthly_required,
            recurrence_frequency="MONTHLY",
            recurrence_start=td,
            recurrence_end=goal.target_date,
            goal_subtype=goal.goal_subtype,
            notes=(
                f"System-derived PMT toward “{goal.name}”. "
                f"Inflation-adjusted target ~{_format_inr(inflation_adjusted_target)}."
            ),
        )
    )

    # L3 — annual checkpoints (cap to avoid huge lists in UI/DB).
    max_checkpoints = min(full_years, 10)
    for y in range(1, max_checkpoints + 1):
        m = 12 * y
        if m > n:
            break
        ms = milestones[y - 1]
        sub_goals.append(
            SubGoalSpec(
                name=f"{goal.name} — Year {y} checkpoint",
                tier="L3",
                goal_type="SAVINGS",
                goal_class="POINT_IN_TIME",
                target_amount=ms.target_corpus,
                target_date=ms.target_date,
                goal_subtype=goal.goal_subtype,
                expected_return_rate=goal.expected_return_rate,
                starting_balance=pv if y == 1 else None,
                notes=f"Corpus target at end of year {y} (milestone).",
            )
        )

    # L2 — single umbrella milestone at mid-horizon or final year if multi-year.
    if full_years >= 2:
        mid = max(1, full_years // 2)
        if mid <= len(milestones):
            ms = milestones[mid - 1]
            sub_goals.insert(
                1,
                SubGoalSpec(
                    name=f"{goal.name} — Mid-horizon milestone (year {mid})",
                    tier="L2",
                    goal_type="SAVINGS",
                    goal_class="POINT_IN_TIME",
                    target_amount=ms.target_corpus,
                    target_date=ms.target_date,
                    goal_subtype=goal.goal_subtype,
                    notes="Intermediate corpus checkpoint (L2).",
                ),
            )

    return DecompositionResult(
        parent_goal_id=goal.id,
        mode="POINT_IN_TIME",
        sub_goals=sub_goals,
        monthly_required=monthly_required,
        inflation_adjusted_target=inflation_adjusted_target,
        reality_check=_reality_check_status(monthly_required, surplus),
        milestones=milestones,
    )


def decompose_debt_goal(goal: Goal, loan_params: LoanParams) -> DecompositionResult:
    """Split purchase into down payment (lump sum) + EMI (recurring)."""
    if goal.target_date is None:
        raise ValueError("target_date is required (purchase / loan start)")

    dp = loan_params.total_cost * loan_params.down_payment_pct
    principal = loan_params.total_cost - dp
    n_months = loan_params.loan_tenure_years * 12
    r_m = loan_params.loan_interest_rate / 12.0 / 100.0

    if principal <= 0:
        emi = 0.0
    elif r_m > 0:
        emi = principal * r_m * (1.0 + r_m) ** n_months / ((1.0 + r_m) ** n_months - 1.0)
    else:
        emi = principal / n_months

    purchase_date = goal.target_date
    loan_end = add_calendar_years(purchase_date, loan_params.loan_tenure_years)

    subtype = goal.goal_subtype or "HOME_PURCHASE"

    sub_goals = [
        SubGoalSpec(
            name=f"{goal.name} — Down payment",
            tier="L2",
            goal_type="SAVINGS",
            goal_class="POINT_IN_TIME",
            target_amount=dp,
            target_date=purchase_date,
            goal_subtype=subtype,
            notes="Lump sum before loan disbursement.",
        ),
        SubGoalSpec(
            name=f"{goal.name} — EMI",
            tier="L2",
            goal_type="DEBT_PAYOFF",
            goal_class="RECURRING_CASH_FLOW",
            recurrence_amount=emi,
            recurrence_frequency="MONTHLY",
            recurrence_start=purchase_date,
            recurrence_end=loan_end,
            goal_subtype="LOAN_PAYOFF",
            notes=(
                f"EMI at {loan_params.loan_interest_rate:.2f}% for "
                f"{loan_params.loan_tenure_years} years on principal "
                f"{_format_inr(principal)}."
            ),
        ),
    ]

    return DecompositionResult(
        parent_goal_id=goal.id,
        mode="DEBT",
        sub_goals=sub_goals,
        monthly_required=emi,
        inflation_adjusted_target=None,
        reality_check=None,
        milestones=[],
    )


def suggest_goals_from_patterns(session: Session, user_id: str) -> list[GoalSuggestion]:
    """Infer goal ideas from active OUTFLOW recurring patterns."""
    q = (
        select(RecurringPattern)
        .where(RecurringPattern.user_id == user_id)
        .where(RecurringPattern.is_active == True)  # noqa: E712
        .where(RecurringPattern.direction == "OUTFLOW")
        .order_by(col(RecurringPattern.id))
    )
    rows = list(session.exec(q).all())
    logger.debug(
        "suggest_goals_from_patterns: user has %d active OUTFLOW patterns to score",
        len(rows),
    )
    out: list[GoalSuggestion] = []

    for p in rows:
        cat = (p.counterparty_category or "").strip()
        freq = (p.frequency or "").upper()
        amt = float(p.expected_amount)
        mc = int(p.match_count or 0)
        conf: Confidence = "HIGH" if mc > 6 else "MEDIUM"

        # EMI / insurance-like
        if cat in (_CAT_FINANCIAL_SERVICES, _CAT_FEES) and freq == "MONTHLY" and amt > 5000:
            safe_name = _title_from_counterparty(p.counterparty)
            out.append(
                GoalSuggestion(
                    source_pattern_id=p.id or 0,
                    counterparty=p.counterparty,
                    suggested_name=f"{safe_name} — EMI or premium",
                    goal_class="RECURRING_CASH_FLOW",
                    goal_type="DEBT_PAYOFF",
                    recurrence_amount=amt,
                    recurrence_frequency="MONTHLY",
                    goal_subtype="LOAN_PAYOFF",
                    confidence=conf,
                    message=(
                        f"Recurring ~{_format_inr(amt)}/mo to “{p.counterparty}”. "
                        "Track as a loan/insurance cash-flow goal?"
                    ),
                )
            )
            continue

        # SIP → growth
        if cat == _CAT_ASSET_MARKETS and freq == "MONTHLY":
            out.append(
                GoalSuggestion(
                    source_pattern_id=p.id or 0,
                    counterparty=p.counterparty,
                    suggested_name="SIP / investment target",
                    goal_class="POINT_IN_TIME",
                    goal_type="INVESTMENT",
                    recurrence_amount=amt,
                    recurrence_frequency="MONTHLY",
                    goal_subtype="CUSTOM",
                    confidence=conf,
                    message=(
                        f"You're investing ~{_format_inr(amt)}/mo via “{p.counterparty}”. "
                        "Set a growth corpus target?"
                    ),
                )
            )
            continue

        # Travel fund
        if (
            cat == _CAT_TRAVEL
            and freq in ("QUARTERLY", "YEARLY")
            and amt > 100_000
        ):
            annual = amt * (4.0 if freq == "QUARTERLY" else 1.0)
            rf = "QUARTERLY" if freq == "QUARTERLY" else "ANNUAL"
            out.append(
                GoalSuggestion(
                    source_pattern_id=p.id or 0,
                    counterparty=p.counterparty,
                    suggested_name="Travel fund",
                    goal_class="RECURRING_CASH_FLOW",
                    goal_type="SAVINGS",
                    recurrence_amount=amt,
                    recurrence_frequency=rf,
                    goal_subtype="TRAVEL",
                    confidence="MEDIUM",
                    message=(
                        f"You spend ~{_format_inr(annual)}/year on travel ({freq.lower()}). "
                        "Plan for this explicitly?"
                    ),
                )
            )
            continue

        # Skip pure baseline expenses
        if cat in _SKIP_GOAL_CATEGORIES:
            continue

    return out


def _title_from_counterparty(cp: str) -> str:
    """Short display label from counterparty string."""
    s = re.sub(r"\s+", " ", (cp or "").strip())
    if not s:
        return "Recurring payment"
    return s[:80]


def parent_has_decompose_children(session: Session, parent_goal_id: int, user_id: str) -> bool:
    """True if any decomposition child goal already exists for this parent."""
    q = (
        select(Goal.id)
        .where(Goal.parent_goal_id == parent_goal_id)
        .where(Goal.user_id == user_id)
    )
    return session.exec(q).first() is not None


def spec_to_goal_row(
    spec: SubGoalSpec,
    *,
    user_id: str,
    parent_goal_id: int | None = None,
) -> Goal:
    """Build a ``Goal`` ORM object from a ``SubGoalSpec`` (not yet added to session)."""
    # Derive allocation_priority later in API if needed; leave None.
    return Goal(
        name=spec.name,
        goal_type=spec.goal_type,
        target_amount=spec.target_amount,
        target_date=spec.target_date,
        user_id=user_id,
        parent_goal_id=parent_goal_id,
        tier=spec.tier,
        goal_class=spec.goal_class,
        recurrence_amount=spec.recurrence_amount,
        recurrence_frequency=spec.recurrence_frequency,
        recurrence_start=spec.recurrence_start,
        recurrence_end=spec.recurrence_end,
        goal_subtype=spec.goal_subtype,
        expected_return_rate=spec.expected_return_rate,
        starting_balance=spec.starting_balance,
        notes=spec.notes,
        activation_status="ACTIVE",
        status="ON_TRACK",
        funding_mode="ACCUMULATION",
        time_horizon="MULTI_YEAR" if spec.tier in ("L2", "L3") else "MONTHLY",
    )
