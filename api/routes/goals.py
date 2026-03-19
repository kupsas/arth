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
    user_id: str = "sashank"
    current_value: float | None = None
    notes: str | None = None


class GoalUpdate(BaseModel):
    name: str | None = None
    target_amount: float | None = None
    target_date: str | None = None
    priority: int | None = None
    linked_category: str | None = None
    current_value: float | None = None
    status: str | None = None               # allow manual status override (e.g. PAUSED)
    notes: str | None = None


_VALID_GOAL_TYPES = {
    "SAVINGS", "EXPENSE_LIMIT", "EMERGENCY_FUND",
    "INVESTMENT", "DEBT_PAYOFF", "INSURANCE", "TAX",
}

_VALID_STATUSES = {"ON_TRACK", "AT_RISK", "BEHIND", "ACHIEVED", "PAUSED"}


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

    goal = Goal(
        name=body.name,
        goal_type=body.goal_type,
        target_amount=body.target_amount,
        target_date=target_date,
        target_metric=body.target_metric,
        priority=body.priority,
        linked_layer=body.linked_layer,
        linked_category=body.linked_category,
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
