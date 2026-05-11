"""
Sim-on-write cache for per-goal simulation progress (Track 3).

``refresh_goal_statuses`` runs the same full multi-goal :func:`simulate` used by the
sandbox, then persists one :class:`api.models.GoalStatusCache` row per projected goal.
Dashboard ``GET /api/goals`` reads these rows via :func:`api.services.goal_evaluator.compute_progress`
so every page load does not re-run a 240-month projection.

Invalidation: callers delete all rows for a ``user_id`` when goals or bank data change;
:func:`simulation_fingerprint` also embeds txn / goal / holding rev counters so a stale
hash triggers a synchronous refresh on the next read.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from api.models import Goal, GoalStatusCache, Holding, Transaction
from api.routes.simulate import build_simulation_params_from_db
from api.services.goal_decomposer import months_between
from api.services.priority_scorer import _effective_goal_class
from api.services.simulation import (
    GC_POINT,
    GC_RECURRING,
    GoalProjection,
    _recurrence_period_months,
    simulate,
)

logger = logging.getLogger(__name__)


def _json_safe(obj: Any) -> Any:
    """Recursively convert date/datetime for JSON dumps (defensive)."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    return obj


def _slim_projection_dict(p: GoalProjection) -> dict[str, Any]:
    """Projection fields for API / storage — drop heavy monthly_trajectory."""
    raw = p.model_dump(mode="json")
    raw.pop("monthly_trajectory", None)
    return raw


def _headline_percentage(goal_class: str, p: GoalProjection) -> float | None:
    """Cache headline % for stale snapshots; API read path overrides PIT with user corpus ratio."""
    gc = goal_class.strip().upper()
    if gc == GC_RECURRING:
        return p.periods_met_pct
    if gc == GC_POINT:
        return p.projected_completion_pct
    return p.projected_completion_pct if p.projected_completion_pct is not None else p.periods_met_pct


def simulation_fingerprint(session: Session, user_id: str) -> str:
    """Stable hash of everything that changes full-simulation inputs for this user."""
    goals = list(
        session.exec(select(Goal).where(Goal.user_id == user_id).order_by(col(Goal.id))).all()
    )
    goal_parts: list[tuple[Any, ...]] = []
    for g in goals:
        goal_parts.append(
            (
                g.id,
                g.updated_at.isoformat() if g.updated_at else None,
                g.activation_status,
                (g.goal_class or "").strip().upper() or None,
                g.goal_type,
                g.target_amount,
                g.target_date.isoformat() if g.target_date else None,
                g.current_value,
                g.starting_balance,
                g.recurrence_amount,
                (g.recurrence_frequency or "").upper() or None,
                g.recurrence_start.isoformat() if g.recurrence_start else None,
                g.recurrence_end.isoformat() if g.recurrence_end else None,
                g.allocation_priority,
                g.monthly_allocation,
                g.expected_return_rate,
                g.goal_specific_inflation_rate,
                g.system_priority_score,
            )
        )

    txn_count = int(
        session.exec(
            select(func.count())
            .select_from(Transaction)
            .where(col(Transaction.user_id) == user_id)
        ).one()
    )
    txn_max_id = session.exec(
        select(func.max(Transaction.id)).where(col(Transaction.user_id) == user_id)
    ).one()
    txn_max_updated = session.exec(
        select(func.max(Transaction.updated_at)).where(col(Transaction.user_id) == user_id)
    ).one()

    hold_stmt = select(Holding.id, Holding.current_value, Holding.updated_at).where(
        Holding.user_id == user_id
    )
    holding_parts: list[tuple[Any, ...]] = []
    for hid, cv, u in session.exec(hold_stmt).all():
        holding_parts.append(
            (
                hid,
                round(float(cv or 0.0), 4) if cv is not None else None,
                u.isoformat() if isinstance(u, datetime.datetime) else None,
            )
        )
    holding_parts.sort()

    payload = {
        "user_id": user_id,
        "as_of_calendar": datetime.date.today().isoformat(),
        "goals": goal_parts,
        "txn_count": txn_count,
        "txn_max_id": txn_max_id,
        "txn_max_updated": txn_max_updated.isoformat()
        if isinstance(txn_max_updated, datetime.datetime)
        else None,
        "holdings": holding_parts,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def invalidate_goal_status_cache(session: Session, user_id: str) -> None:
    """Drop all cached rows for a user (call after goal edits or pipeline txn ingest)."""
    session.exec(delete(GoalStatusCache).where(col(GoalStatusCache.user_id) == user_id))


def delete_goal_status_row_for_goal(session: Session, goal_id: int) -> None:
    """Remove the cache row for one goal before deleting the goal row (FK order)."""
    session.exec(delete(GoalStatusCache).where(col(GoalStatusCache.goal_id) == goal_id))


def refresh_goal_statuses(
    session: Session,
    user_id: str,
    *,
    force: bool = False,
    simulation_months: int = 240,
    surplus_trailing_months: int = 6,
) -> int:
    """Run full simulation and rewrite ``goal_status_cache`` for *user_id*.

    When ``force`` is False and an existing row already matches
    :func:`simulation_fingerprint`, returns early without re-simulating (0 rows touched
    logic: we still return the number of goals written when skipped — return -1 vs 0;
    simpler: return number of cache rows after operation).

    Returns the number of cache rows inserted for this user.
    """
    fp = simulation_fingerprint(session, user_id)

    if not force:
        rows_existing = list(
            session.exec(select(GoalStatusCache).where(GoalStatusCache.user_id == user_id)).all()
        )
        if rows_existing and rows_existing[0].simulation_hash == fp:
            return len(rows_existing)

    params, _meta = build_simulation_params_from_db(
        session,
        user_id,
        simulation_months=simulation_months,
        surplus_trailing_months=surplus_trailing_months,
    )
    try:
        result = simulate(params)
    except Exception:
        logger.exception("goal_status_cache: simulate() failed for user_id=%r", user_id)
        raise

    invalidate_goal_status_cache(session, user_id)

    now = datetime.datetime.now(datetime.UTC)
    n = 0
    for p in result.projections:
        gid = p.goal_id
        if gid is None:
            continue
        db_goal = session.get(Goal, gid)
        if db_goal is None or db_goal.user_id != user_id:
            continue
        eff_class = _effective_goal_class(db_goal)
        headline = _headline_percentage(eff_class, p)
        if headline is None:
            headline = 0.0
        slim = _slim_projection_dict(p)
        slim["effective_goal_class"] = eff_class
        row = GoalStatusCache(
            goal_id=gid,
            user_id=user_id,
            goal_class=eff_class,
            percentage=float(headline),
            status_data=json.dumps(_json_safe(slim)),
            simulation_hash=fp,
            computed_at=now,
        )
        session.add(row)
        n += 1

    session.flush()
    return n


def get_cache_row(session: Session, goal_id: int) -> GoalStatusCache | None:
    return session.exec(select(GoalStatusCache).where(GoalStatusCache.goal_id == goal_id)).first()


def _point_in_time_saved_amount(goal: Goal) -> float:
    """Corpus from the goal row: inline Update progress (current_value) first, else starting_balance."""
    if goal.current_value is not None:
        return float(goal.current_value)
    if goal.starting_balance is not None:
        return float(goal.starting_balance)
    return 0.0


def _point_in_time_funding_pct(
    goal: Goal,
    status_data: dict[str, Any],
    eff_class: str,
) -> float | None:
    """Already saved ÷ inflation-adjusted target at deadline × 100; ``None`` if not applicable."""
    if eff_class != GC_POINT:
        return None
    saved = _point_in_time_saved_amount(goal)
    infl_t = status_data.get("inflation_adjusted_target_at_deadline")
    if infl_t is not None and float(infl_t) > 1e-9:
        return (saved / float(infl_t)) * 100.0
    raw = goal.target_amount
    if raw is not None and float(raw) > 1e-9:
        return (saved / float(raw)) * 100.0
    return None


def _recurring_timeline_pct(
    goal: Goal,
    today: datetime.date | None = None,
) -> float | None:
    """Elapsed billing periods ÷ scheduled periods from recurrence dates (calendar), not simulator."""
    if goal.recurrence_start is None:
        return None

    td = (today or datetime.date.today()).replace(day=1)
    anchor = goal.recurrence_start.replace(day=1)
    pm = _recurrence_period_months(goal.recurrence_frequency)

    end_m = goal.recurrence_end.replace(day=1) if goal.recurrence_end else None

    if end_m is not None:
        if td < anchor:
            return 0.0
        if end_m < anchor:
            return 100.0

        total_months_span = months_between(anchor, end_m) + 1
        total_periods = max(1, (total_months_span + pm - 1) // pm)

        if td >= end_m:
            return 100.0

        idx = months_between(anchor, td)
        elapsed_periods = min(idx // pm, total_periods)
        return min(100.0, 100.0 * elapsed_periods / total_periods)

    # Open-ended "Until": wall-clock progress vs a 10-year horizon (no simulation contribution %).
    REF_MONTHS = 120
    if td < anchor:
        return 0.0
    cm = months_between(anchor, td)
    cm_capped = min(cm, REF_MONTHS)
    return min(100.0, 100.0 * cm_capped / REF_MONTHS)


def progress_from_cache_or_naive(goal: Goal, session: Session) -> dict[str, Any]:
    """Build the ``compute_progress`` dict using the cache when fingerprint matches."""
    uid = goal.user_id
    gid = goal.id
    if gid is None:
        return _naive_non_expense_progress(goal)

    fp = simulation_fingerprint(session, uid)
    row = get_cache_row(session, gid)

    if row is None or row.simulation_hash != fp:
        try:
            refresh_goal_statuses(session, uid, force=True)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("goal_status_cache: refresh failed; falling back to naive for goal %s", goal.id)
            return _naive_non_expense_progress(goal)
        row = get_cache_row(session, gid)
        if row is None:
            return _naive_non_expense_progress(goal)

    try:
        status_data = json.loads(row.status_data)
    except json.JSONDecodeError:
        status_data = {}

    eff = (status_data.get("effective_goal_class") or row.goal_class or "").strip().upper()
    if not eff:
        eff = _effective_goal_class(goal)

    pit_pct = _point_in_time_funding_pct(goal, status_data, eff)
    pct = float(row.percentage)
    if eff == GC_POINT and pit_pct is not None:
        pct = float(pit_pct)
    elif eff == GC_RECURRING:
        rec_tl = _recurring_timeline_pct(goal)
        if rec_tl is not None:
            pct = float(rec_tl)

    current_value = _current_value_from_status(status_data, eff, goal)

    out: dict[str, Any] = {
        "current_value": round(float(current_value), 2),
        "target_amount": goal.target_amount,
        "percentage": round(pct, 1),
        "status_data": status_data,
        "projected_completion_pct": status_data.get("projected_completion_pct"),
        "periods_met_pct": status_data.get("periods_met_pct"),
    }
    return out


def _current_value_from_status(status_data: dict[str, Any], eff_class: str, goal: Goal) -> float:
    """Scalar shown as Spent / Saved on the goals list."""
    if eff_class == GC_RECURRING:
        tc = status_data.get("total_contributed")
        if tc is not None:
            return float(tc)
    if eff_class == GC_POINT:
        return _point_in_time_saved_amount(goal)
    cps = status_data.get("corpus_at_deadline")
    if cps is not None:
        return float(cps)
    return float(goal.current_value or 0.0)


def _naive_non_expense_progress(goal: Goal) -> dict[str, Any]:
    """Last-resort ratio when simulation or cache is unavailable (e.g. engine error)."""
    eff = _effective_goal_class(goal)
    if eff == GC_POINT:
        saved = _point_in_time_saved_amount(goal)
        target = float(goal.target_amount or 0.0)
        pct = (saved / target) * 100.0 if target > 1e-9 else 0.0
        return {
            "current_value": round(saved, 2),
            "target_amount": goal.target_amount,
            "percentage": round(pct, 1),
            "status_data": None,
            "projected_completion_pct": None,
            "periods_met_pct": None,
        }
    if eff == GC_RECURRING:
        rp = _recurring_timeline_pct(goal)
        if rp is not None:
            cur = float(goal.current_value or 0.0)
            return {
                "current_value": round(cur, 2),
                "target_amount": goal.target_amount,
                "percentage": round(rp, 1),
                "status_data": None,
                "projected_completion_pct": None,
                "periods_met_pct": None,
            }
    current_value = float(goal.current_value or 0.0)
    target = float(goal.target_amount or 0.0)
    pct = (current_value / target) * 100.0 if target > 0 else 0.0
    return {
        "current_value": round(current_value, 2),
        "target_amount": goal.target_amount,
        "percentage": round(pct, 1),
        "status_data": None,
        "projected_completion_pct": None,
        "periods_met_pct": None,
    }
