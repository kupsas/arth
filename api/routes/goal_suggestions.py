"""
Pattern-based goal suggestions (Sub-Plan D).

GET /api/goal-suggestions — inferred goals from RecurringPattern rows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.database import get_session
from api.services.goal_decomposer import suggest_goals_from_patterns
from sqlmodel import Session

router = APIRouter()


@router.get("")
def list_goal_suggestions(
    user_id: str | None = Query(
        None,
        description="Defaults to the authenticated user; must match session when set.",
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> list[dict]:
    """Return heuristic goal ideas from active recurring OUTFLOW patterns."""
    uid = user_id.strip() if user_id and user_id.strip() else current_user
    if uid != current_user:
        # Single-user product: do not leak other users' pattern-derived suggestions.
        raise HTTPException(
            status_code=403,
            detail="user_id must match the authenticated user.",
        )
    rows = suggest_goals_from_patterns(session, uid)
    return [r.model_dump(mode="json") for r in rows]
