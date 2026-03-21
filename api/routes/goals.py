"""
Goals CRUD endpoints — Phase 4.5d

POST   /api/goals           — create a goal
GET    /api/goals           — list goals (filterable by user_id, goal_type, status)
GET    /api/goals/{id}      — single goal with computed progress
PATCH  /api/goals/{id}      — update goal fields or current_value
DELETE /api/goals/{id}      — delete a goal

Progress computation:
  - EXPENSE_LIMIT goals: auto-computed from transactions DB (current month spend)
  - All other goal types: use goal.current_value vs goal.target_amount
  - Status: ON_TRACK | AT_RISK | BEHIND | ACHIEVED | PAUSED
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from api.database import get_session
from api.models import Goal
from api.services.chart_metrics import (
    CHART_KEY_EXPENSE_NEED_WANT_STACK,
    CHART_KEY_INVESTMENT_NET,
    validate_chart_key_for_goal,
)
from api.services.goal_evaluator import compute_progress

logger = logging.getLogger(__name__)
router = APIRouter()


# ───────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ───────────────────────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    name: str
    goal_type: str                          # e.g. "EXPENSE_LIMIT", "SAVINGS", etc.
    target_amount: float | None = None
    target_date: str | None = None          # "YYYY-MM-DD" or null
    target_metric: str | None = None        # JSON blob for complex conditions
    priority: int = 3                       # 1 (highest) to 5 (lowest)
    linked_layer: int = 3                   # 1-5
    linked_category: str | None = None      # e.g. "Food & Dining"
    chart_key: str | None = None            # dashboard binding (optional)
    progress_cadence: str | None = None     # MONTHLY (default) | ANNUAL (EXPENSE_LIMIT only)
    user_id: str = "sashank"
    current_value: float | None = None
    notes: str | None = None


class GoalUpdate(BaseModel):
    name: str | None = None
    target_amount: float | None = None
    target_date: str | None = None
    priority: int | None = None
    linked_category: str | None = None
    chart_key: str | None = None
    progress_cadence: str | None = None
    current_value: float | None = None
    status: str | None = None               # allow manual status override (e.g. PAUSED)
    notes: str | None = None


_VALID_GOAL_TYPES = {
    "SAVINGS", "EXPENSE_LIMIT", "EMERGENCY_FUND",
    "INVESTMENT", "DEBT_PAYOFF", "INSURANCE", "TAX",
}

_VALID_STATUSES = {"ON_TRACK", "AT_RISK", "BEHIND", "ACHIEVED", "PAUSED"}

_VALID_PROGRESS_CADENCE = {"MONTHLY", "ANNUAL"}


def _validate_progress_cadence(goal_type: str, cadence: str | None) -> str:
    """Default MONTHLY; ANNUAL only for EXPENSE_LIMIT."""
    raw = (cadence or "MONTHLY").strip().upper()
    if raw not in _VALID_PROGRESS_CADENCE:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid progress_cadence: {cadence!r}. Use MONTHLY or ANNUAL.",
        )
    if raw == "ANNUAL" and goal_type != "EXPENSE_LIMIT":
        raise HTTPException(
            status_code=400,
            detail="progress_cadence ANNUAL is only allowed for EXPENSE_LIMIT goals.",
        )
    return raw


def _default_chart_key_on_create(goal_type: str, linked_category: str | None, chart_key: str | None) -> str | None:
    """Apply sensible defaults when the client omits chart_key."""
    if chart_key is not None:
        return chart_key
    if goal_type == "INVESTMENT":
        return CHART_KEY_INVESTMENT_NET
    if goal_type == "EXPENSE_LIMIT" and linked_category is None:
        return CHART_KEY_EXPENSE_NEED_WANT_STACK
    return None


def _ensure_chart_key_unique(
    session: Session,
    user_id: str,
    chart_key: str | None,
    *,
    exclude_goal_id: int | None = None,
) -> None:
    if chart_key is None:
        return
    q = select(Goal).where(Goal.user_id == user_id).where(Goal.chart_key == chart_key)
    if exclude_goal_id is not None:
        q = q.where(Goal.id != exclude_goal_id)
    if session.exec(q).first():
        raise HTTPException(
            status_code=400,
            detail=f"Another goal already uses chart_key {chart_key!r} for this user.",
        )


# ───────────────────────────────────────────────────────────────────────────
# POST / — create a goal
# ───────────────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_goal(body: GoalCreate, *, session: Session = Depends(get_session)) -> dict:
    """Create a new financial goal."""
    if body.goal_type not in _VALID_GOAL_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid goal_type: {body.goal_type!r}. Valid: {sorted(_VALID_GOAL_TYPES)}",
        )

    target_date = None
    if body.target_date:
        try:
            target_date = datetime.date.fromisoformat(body.target_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid target_date format: {body.target_date!r}. Use YYYY-MM-DD.",
            )

    resolved_ck = _default_chart_key_on_create(
        body.goal_type, body.linked_category, body.chart_key
    )
    try:
        validate_chart_key_for_goal(body.goal_type, resolved_ck)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _ensure_chart_key_unique(session, body.user_id, resolved_ck)

    pc = _validate_progress_cadence(body.goal_type, body.progress_cadence)

    goal = Goal(
        name=body.name,
        goal_type=body.goal_type,
        target_amount=body.target_amount,
        target_date=target_date,
        target_metric=body.target_metric,
        priority=body.priority,
        linked_layer=body.linked_layer,
        linked_category=body.linked_category,
        chart_key=resolved_ck,
        progress_cadence=pc,
        user_id=body.user_id,
        current_value=body.current_value,
        notes=body.notes,
    )
    session.add(goal)
    session.commit()
    session.refresh(goal)

    progress = compute_progress(goal, session)
    return _goal_to_dict(goal, progress)


# ───────────────────────────────────────────────────────────────────────────
# GET / — list goals
# ───────────────────────────────────────────────────────────────────────────

@router.get("")
def list_goals(
    user_id: str | None = Query(None),
    goal_type: str | None = Query(None),
    status: str | None = Query(None),
    *,
    session: Session = Depends(get_session),
) -> list[dict]:
    """List all goals, optionally filtered by user_id, goal_type, or status."""
    query = select(Goal)

    if user_id is not None:
        query = query.where(Goal.user_id == user_id)
    if goal_type is not None:
        query = query.where(Goal.goal_type == goal_type)
    if status is not None:
        query = query.where(Goal.status == status)

    query = query.order_by(col(Goal.priority), col(Goal.created_at))
    goals = session.exec(query).all()

    result = []
    for goal in goals:
        progress = compute_progress(goal, session)
        result.append(_goal_to_dict(goal, progress))
    return result


# ───────────────────────────────────────────────────────────────────────────
# GET /{id} — single goal with computed progress
# ───────────────────────────────────────────────────────────────────────────

@router.get("/{goal_id}")
def get_goal(goal_id: int, *, session: Session = Depends(get_session)) -> dict:
    """Get a single goal with live-computed progress."""
    goal = session.get(Goal, goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    progress = compute_progress(goal, session)
    return _goal_to_dict(goal, progress)


# ───────────────────────────────────────────────────────────────────────────
# PATCH /{id} — update a goal
# ───────────────────────────────────────────────────────────────────────────

@router.patch("/{goal_id}")
def update_goal(
    goal_id: int,
    body: GoalUpdate,
    *,
    session: Session = Depends(get_session),
) -> dict:
    """Update mutable fields on a goal.

    The most common use: set current_value (manual progress update) or
    change status to PAUSED / ACHIEVED if you want to override auto-computation.
    """
    goal = session.get(Goal, goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    update_data = body.model_dump(exclude_unset=True)

    if "status" in update_data and update_data["status"] not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {update_data['status']!r}. Valid: {sorted(_VALID_STATUSES)}",
        )

    if "target_date" in update_data and update_data["target_date"] is not None:
        try:
            update_data["target_date"] = datetime.date.fromisoformat(update_data["target_date"])
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid target_date format. Use YYYY-MM-DD.",
            )

    if "chart_key" in update_data:
        ck = update_data["chart_key"]
        try:
            validate_chart_key_for_goal(goal.goal_type, ck)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        _ensure_chart_key_unique(
            session, goal.user_id, ck, exclude_goal_id=goal.id
        )

    if "progress_cadence" in update_data:
        update_data["progress_cadence"] = _validate_progress_cadence(
            goal.goal_type, update_data["progress_cadence"]
        )

    for field, value in update_data.items():
        setattr(goal, field, value)

    goal.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(goal)
    session.commit()
    session.refresh(goal)

    progress = compute_progress(goal, session)
    return _goal_to_dict(goal, progress)


# ───────────────────────────────────────────────────────────────────────────
# DELETE /{id} — delete a goal
# ───────────────────────────────────────────────────────────────────────────

@router.delete("/{goal_id}", status_code=204)
def delete_goal(goal_id: int, *, session: Session = Depends(get_session)) -> None:
    """Permanently delete a goal."""
    goal = session.get(Goal, goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    session.delete(goal)
    session.commit()


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _goal_to_dict(goal: Goal, progress: dict) -> dict:
    """Serialise a Goal + its computed progress to a JSON-safe dict."""
    return {
        "id": goal.id,
        "name": goal.name,
        "goal_type": goal.goal_type,
        "target_amount": goal.target_amount,
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "target_metric": goal.target_metric,
        "priority": goal.priority,
        "linked_layer": goal.linked_layer,
        "linked_category": goal.linked_category,
        "chart_key": goal.chart_key,
        "progress_cadence": goal.progress_cadence,
        "user_id": goal.user_id,
        "current_value": goal.current_value,
        "notes": goal.notes,
        # Computed progress fields
        "computed_current_value": progress["current_value"],
        "computed_percentage": progress["percentage"],
        "status": progress["status"],         # always return the derived status
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
        "updated_at": goal.updated_at.isoformat() if goal.updated_at else None,
    }
