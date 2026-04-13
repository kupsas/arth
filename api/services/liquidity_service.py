"""
Holding liquidity dates for goals / simulation (Sub-Plan C).

Computes ``earliest_liquidity_date`` per :class:`~api.models.Holding`, refreshes in
batch (liquid sleeves move with ``today``), matches holdings to goals by date, and
builds time-horizon summaries. Does **not** allocate holdings to goals — only
\"what is accessible before the goal date\" for suggestions and warnings.

See ``docs/personal-data/thoughts_on_goals.md`` §4.
"""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api.models import Goal, Holding
from api.services.net_worth import holding_value
from api.services.nps_exit_projection import nps_normal_exit_date, parse_subscriber_dob_from_env
from api.services.ppf_maturity import effective_ppf_maturity_date
from pipeline.models import AssetClass

# Conservative sentinel when we cannot infer a real liquidity date (under-count accessible).
_FAR_FUTURE = datetime.date(2099, 12, 31)

# Goal class from goals architecture V2 — Growth goals have no deadline for matching.
_GROWTH_CLASS = "GROWTH"


def add_business_days(start: datetime.date, n: int) -> datetime.date:
    """Add *n* business days (Mon–Fri); no holiday calendar (India/NSE holidays not modeled)."""
    d = start
    added = 0
    while added < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def compute_earliest_liquidity_date(session: Session, h: Holding, today: datetime.date) -> datetime.date:
    """
    Earliest calendar date on which this holding's value is treated as accessible.

    Rules (V1 — refines migration V0 heuristics):

    - **SAVINGS / GOLD:** today.
    - **EQUITY, MUTUAL_FUND, ESOP:** today + 2 business days (T+2 settlement).
    - **FD:** if maturity within 7 days (or past), maturity; else today + 7 days (breakable FD).
    - **PPF:** ``effective_ppf_maturity_date`` (statutory from ledger when BUY rows exist; else stored).
    - **NPS:** stored ``maturity_date``, else 60th birthday from ``DOB`` env, else far future.
    - **SGB:** maturity date, else far future.
    - **REAL_ESTATE / OTHER:** maturity or far future.

    :param session: DB session (needed for PPF effective maturity from ledger).
    :param h: Holding row (``asset_class``, ``maturity_date``, …).
    :param today: Valuation \"as of\" date (usually UTC today in API).
    """
    ac = (h.asset_class or "").strip().upper()
    mat = h.maturity_date
    week_ahead = today + datetime.timedelta(days=7)

    if ac == AssetClass.SAVINGS.value:
        return today
    if ac in (
        AssetClass.EQUITY.value,
        AssetClass.MUTUAL_FUND.value,
        AssetClass.ESOP.value,
    ):
        return add_business_days(today, 2)
    if ac == AssetClass.GOLD.value:
        return today
    if ac == AssetClass.SOVEREIGN_GOLD_BOND.value:
        return mat if mat is not None else _FAR_FUTURE
    if ac == AssetClass.FD.value:
        if mat is not None and mat <= week_ahead:
            return mat
        return today + datetime.timedelta(days=7)
    if ac == AssetClass.PPF.value:
        eff = effective_ppf_maturity_date(
            session,
            holding_id=h.id,
            stored_maturity=mat,
            asset_class=ac,
        )
        return eff if eff is not None else _FAR_FUTURE
    if ac == AssetClass.NPS.value:
        if mat is not None:
            return mat
        dob = parse_subscriber_dob_from_env()
        if dob is not None:
            return nps_normal_exit_date(dob)
        return _FAR_FUTURE
    if ac in (AssetClass.REAL_ESTATE.value, AssetClass.OTHER.value):
        return mat if mat is not None else _FAR_FUTURE
    return mat if mat is not None else _FAR_FUTURE


def liquidity_bucket_label(liquidity_date: datetime.date, today: datetime.date) -> str:
    """Short human label for a single holding (aligned with summary buckets)."""
    if liquidity_date <= today:
        return "Available now"
    delta_days = (liquidity_date - today).days
    if delta_days <= 7:
        return "Within a week"
    if delta_days <= 30:
        return "Within 1 month"
    if delta_days <= 90:
        return "Within 3 months"
    if delta_days <= 365:
        return "Within 1 year"
    if delta_days <= 5 * 365:
        return "1–5 years"
    return "5+ years"


def _summary_bucket_id(
    liquidity_date: datetime.date, today: datetime.date
) -> Literal[
    "available_now",
    "within_week",
    "within_1m",
    "within_3m",
    "within_1y",
    "y1_to_y5",
    "y5_plus",
]:
    """Assign one portfolio summary bucket per liquidity date."""
    if liquidity_date <= today:
        return "available_now"
    delta_days = (liquidity_date - today).days
    if delta_days <= 7:
        return "within_week"
    if delta_days <= 30:
        return "within_1m"
    if delta_days <= 90:
        return "within_3m"
    if delta_days <= 365:
        return "within_1y"
    if delta_days <= 5 * 365:
        return "y1_to_y5"
    return "y5_plus"


_SUMMARY_BUCKET_META: dict[str, tuple[str, str]] = {
    "available_now": ("Available now", "On or before today"),
    "within_week": ("Within a week", "Next 7 calendar days"),
    "within_1m": ("Within 1 month", "8–30 days ahead"),
    "within_3m": ("Within 3 months", "31–90 days ahead"),
    "within_1y": ("Within 1 year", "91–365 days ahead"),
    "y1_to_y5": ("1–5 years", "366 days–5 years ahead"),
    "y5_plus": ("5+ years", "More than 5 years ahead"),
}


class HoldingLiquidityDetail(BaseModel):
    """One holding with resolved liquidity date and a readable bucket."""

    holding_id: int
    name: str
    asset_class: str
    current_value: float = Field(..., description="INR — same rules as net worth")
    earliest_liquidity_date: datetime.date
    liquidity_bucket: str = Field(..., description="Human label, e.g. 'Within a week'")


class LiquidityBucket(BaseModel):
    """Aggregate value in one time-horizon bucket."""

    bucket_id: str
    label: str
    date_range_hint: str
    total_value_inr: float
    holdings_count: int


class LiquiditySummary(BaseModel):
    """Portfolio liquidity breakdown by time horizon (independent of goals)."""

    user_id: str
    as_of_date: datetime.date
    total_value_inr: float
    buckets: list[LiquidityBucket]


class GoalHoldingMatch(BaseModel):
    """Holdings whose liquidity date is on or before the goal target date."""

    goal_id: int
    goal_name: str
    target_date: datetime.date | None
    matched_holdings: list[HoldingLiquidityDetail]
    total_accessible_value_inr: float


class StartingBalanceSuggestion(BaseModel):
    """Suggested starting balance from accessible holdings (informational, not allocation)."""

    goal_id: int
    goal_name: str
    target_date: datetime.date
    suggested_starting_balance_inr: float
    matched_holdings: list[HoldingLiquidityDetail]
    explanation: str


class LiquidityMismatchResult(BaseModel):
    """Whether claimed savings exceed holdings accessible before the goal date."""

    goal_id: int
    goal_name: str
    target_date: datetime.date | None
    claimed_amount_inr: float
    total_accessible_by_target_date_inr: float
    is_mismatch: bool
    shortfall_inr: float | None = Field(
        None, description="Positive if claimed > accessible (how much over)"
    )
    warning_message: str | None = None


class RefreshResult(BaseModel):
    """Outcome of batch liquidity recompute for one user."""

    user_id: str
    as_of_date: datetime.date
    holdings_examined: int
    updated: int
    unchanged: int
    warnings: list[str]


class RefreshAllUsersResult(BaseModel):
    """Scheduler / admin: refresh every distinct ``user_id`` seen on holdings."""

    as_of_date: datetime.date
    user_count: int
    total_updated: int
    total_unchanged: int
    per_user: list[RefreshResult]


def _holding_detail(
    session: Session,
    h: Holding,
    today: datetime.date,
    *,
    liquidity_date: datetime.date | None = None,
) -> HoldingLiquidityDetail:
    """Build detail row; optionally pass precomputed liquidity date."""
    assert h.id is not None
    ld = liquidity_date if liquidity_date is not None else compute_earliest_liquidity_date(session, h, today)
    cv = holding_value(session, h, None)
    return HoldingLiquidityDetail(
        holding_id=h.id,
        name=h.name,
        asset_class=h.asset_class,
        current_value=round(cv, 2),
        earliest_liquidity_date=ld,
        liquidity_bucket=liquidity_bucket_label(ld, today),
    )


def refresh_all_liquidity_dates(session: Session, user_id: str, today: datetime.date | None = None) -> RefreshResult:
    """
    Recompute and persist ``earliest_liquidity_date`` for all **active** holdings of ``user_id``.

    Liquid sleeves (equity/MF) get a new date each run as ``today`` moves.
    """
    uid = user_id.strip() or "sashank"
    as_of = today or datetime.datetime.now(datetime.UTC).date()
    warnings: list[str] = []

    rows = list(
        session.exec(
            select(Holding).where(
                Holding.user_id == uid,
                Holding.is_active == True,  # noqa: E712
            )
        ).all()
    )

    updated = 0
    unchanged = 0
    now = datetime.datetime.now(datetime.UTC)

    for h in rows:
        new_date = compute_earliest_liquidity_date(session, h, as_of)
        old = h.earliest_liquidity_date
        if old != new_date:
            h.earliest_liquidity_date = new_date
            h.updated_at = now
            session.add(h)
            updated += 1
        else:
            unchanged += 1

    if not rows:
        warnings.append("No active holdings for this user — nothing to refresh")

    return RefreshResult(
        user_id=uid,
        as_of_date=as_of,
        holdings_examined=len(rows),
        updated=updated,
        unchanged=unchanged,
        warnings=warnings,
    )


def refresh_all_users_liquidity_dates(session: Session, today: datetime.date | None = None) -> RefreshAllUsersResult:
    """
    Refresh liquidity dates for every distinct ``user_id`` present in ``holdings``.

    Used by the daily scheduler after prices (keeps T+2 dates aligned with calendar).
    """
    as_of = today or datetime.datetime.now(datetime.UTC).date()
    uids_raw = session.exec(select(Holding.user_id).distinct()).all()
    user_ids = sorted({str(u) for u in uids_raw if u is not None})

    per_user: list[RefreshResult] = []
    total_u = 0
    total_c = 0
    for uid in user_ids:
        r = refresh_all_liquidity_dates(session, uid, today=as_of)
        per_user.append(r)
        total_u += r.updated
        total_c += r.unchanged

    return RefreshAllUsersResult(
        as_of_date=as_of,
        user_count=len(per_user),
        total_updated=total_u,
        total_unchanged=total_c,
        per_user=per_user,
    )


def liquidity_summary(session: Session, user_id: str, today: datetime.date | None = None) -> LiquiditySummary:
    """
    Sum portfolio value into time-horizon buckets using **resolved** liquidity dates.

    Holdings with NULL stored date are computed on the fly (not persisted here).
    """
    uid = user_id.strip() or "sashank"
    as_of = today or datetime.datetime.now(datetime.UTC).date()

    rows = list(
        session.exec(
            select(Holding).where(
                Holding.user_id == uid,
                Holding.is_active == True,  # noqa: E712
            )
        ).all()
    )

    totals: dict[str, float] = {k: 0.0 for k in _SUMMARY_BUCKET_META}
    counts: dict[str, int] = {k: 0 for k in _SUMMARY_BUCKET_META}

    grand = 0.0
    for h in rows:
        ld = h.earliest_liquidity_date
        if ld is None:
            ld = compute_earliest_liquidity_date(session, h, as_of)
        bid = _summary_bucket_id(ld, as_of)
        v = holding_value(session, h, None)
        grand += v
        totals[bid] += v
        counts[bid] += 1

    buckets: list[LiquidityBucket] = []
    for bid, (label, hint) in _SUMMARY_BUCKET_META.items():
        buckets.append(
            LiquidityBucket(
                bucket_id=bid,
                label=label,
                date_range_hint=hint,
                total_value_inr=round(totals[bid], 2),
                holdings_count=counts[bid],
            )
        )

    return LiquiditySummary(
        user_id=uid,
        as_of_date=as_of,
        total_value_inr=round(grand, 2),
        buckets=buckets,
    )


def match_holdings_to_goal(session: Session, goal_id: int, user_id: str, today: datetime.date | None = None) -> GoalHoldingMatch:
    """
    Holdings accessible on or before ``goal.target_date``.

    **GROWTH** goals or goals with no ``target_date``: all active holdings (whole portfolio pool).
    """
    uid = user_id.strip() or "sashank"
    as_of = today or datetime.datetime.now(datetime.UTC).date()

    g = session.get(Goal, goal_id)
    if g is None or g.user_id != uid:
        raise ValueError("goal_not_found")

    growth = (g.goal_class or "").strip().upper() == _GROWTH_CLASS
    no_deadline = g.target_date is None

    rows = list(
        session.exec(
            select(Holding).where(
                Holding.user_id == uid,
                Holding.is_active == True,  # noqa: E712
            )
        ).all()
    )

    matched: list[HoldingLiquidityDetail] = []
    total = 0.0

    for h in rows:
        ld = h.earliest_liquidity_date
        if ld is None:
            ld = compute_earliest_liquidity_date(session, h, as_of)

        if growth or no_deadline:
            include = True
        else:
            assert g.target_date is not None
            include = ld <= g.target_date

        if include:
            detail = _holding_detail(session, h, as_of, liquidity_date=ld)
            matched.append(detail)
            total += detail.current_value

    return GoalHoldingMatch(
        goal_id=goal_id,
        goal_name=g.name,
        target_date=g.target_date,
        matched_holdings=matched,
        total_accessible_value_inr=round(total, 2),
    )


def suggest_starting_balances(session: Session, user_id: str, today: datetime.date | None = None) -> list[StartingBalanceSuggestion]:
    """
    For each **ACTIVE** goal with a **target_date**, list accessible holdings and a plain-English blurb.

    Same holding may appear for multiple goals — this is intentional (suggestions, not allocation).
    """
    uid = user_id.strip() or "sashank"
    as_of = today or datetime.datetime.now(datetime.UTC).date()

    goals = list(
        session.exec(
            select(Goal).where(
                Goal.user_id == uid,
                Goal.activation_status == "ACTIVE",
                col(Goal.target_date).is_not(None),
            )
        ).all()
    )

    out: list[StartingBalanceSuggestion] = []
    for g in goals:
        if g.id is None or g.target_date is None:
            continue
        # Skip pure growth-class goals with a date? Unusual; still match by date rules.
        try:
            m = match_holdings_to_goal(session, g.id, uid, today=as_of)
        except ValueError:
            continue

        parts: list[str] = []
        for h in m.matched_holdings[:5]:
            parts.append(
                f"{h.name} ({h.current_value:,.0f} INR, liquidity {h.earliest_liquidity_date.isoformat()})"
            )
        if len(m.matched_holdings) > 5:
            parts.append(f"… and {len(m.matched_holdings) - 5} more")
        expl = (
            f"By {g.target_date.isoformat()}, these holdings are accessible before your goal date — "
            f"combined ~{m.total_accessible_value_inr:,.0f} INR. "
        )
        if parts:
            expl += "Includes: " + "; ".join(parts) + "."
        else:
            expl += "No holdings matched the liquidity window."

        out.append(
            StartingBalanceSuggestion(
                goal_id=g.id,
                goal_name=g.name,
                target_date=g.target_date,
                suggested_starting_balance_inr=m.total_accessible_value_inr,
                matched_holdings=m.matched_holdings,
                explanation=expl,
            )
        )

    return out


def check_liquidity_mismatch(
    session: Session,
    goal_id: int,
    claimed_amount_inr: float,
    user_id: str,
    today: datetime.date | None = None,
) -> LiquidityMismatchResult:
    """
    Compare ``claimed_amount_inr`` (e.g. user-stated savings toward the goal) to
    the sum of holdings accessible **on or before** ``goal.target_date``.

    If ``claimed_amount`` exceeds that sum, returns a warning string (informational).
    """
    uid = user_id.strip() or "sashank"
    as_of = today or datetime.datetime.now(datetime.UTC).date()

    g = session.get(Goal, goal_id)
    if g is None or g.user_id != uid:
        raise ValueError("goal_not_found")

    m = match_holdings_to_goal(session, goal_id, uid, today=as_of)
    accessible = m.total_accessible_value_inr
    eps = 1.0
    over = claimed_amount_inr > accessible + eps
    shortfall = round(claimed_amount_inr - accessible, 2) if over else None

    warn: str | None = None
    if over and shortfall is not None:
        warn = (
            f"You indicated {claimed_amount_inr:,.0f} INR toward “{g.name}”, "
            f"but only about {accessible:,.0f} INR is in holdings we treat as accessible "
            f"before this goal's horizon. Shortfall vs portfolio: {shortfall:,.0f} INR — "
            f"adjust if you have cash outside Arth or different liquidity assumptions."
        )

    return LiquidityMismatchResult(
        goal_id=goal_id,
        goal_name=g.name,
        target_date=g.target_date,
        claimed_amount_inr=round(claimed_amount_inr, 2),
        total_accessible_by_target_date_inr=accessible,
        is_mismatch=over,
        shortfall_inr=shortfall,
        warning_message=warn,
    )
