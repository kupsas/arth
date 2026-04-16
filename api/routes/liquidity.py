"""
Liquidity API — Sub-Plan C.

GET  /api/liquidity/summary           — time-horizon breakdown of portfolio
POST /api/liquidity/refresh           — recompute ``earliest_liquidity_date`` for all active holdings
GET  /api/liquidity/goal-match/{id}   — holdings accessible on/before goal target date
GET  /api/liquidity/goal-suggestions  — starting-balance hints per dated goal
POST /api/liquidity/mismatch-check    — claimed amount vs accessible holdings
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from api.auth import effective_user_id
from api.database import get_session
from api.services.liquidity_service import (
    GoalHoldingMatch,
    LiquidityMismatchResult,
    LiquiditySummary,
    RefreshResult,
    StartingBalanceSuggestion,
    check_liquidity_mismatch,
    liquidity_summary,
    match_holdings_to_goal,
    refresh_all_liquidity_dates,
    suggest_starting_balances,
)

router = APIRouter()


class MismatchCheckBody(BaseModel):
    """Body for POST /mismatch-check."""

    goal_id: int = Field(..., ge=1)
    claimed_amount_inr: float = Field(..., ge=0, description="Amount user claims saved toward this goal")


@router.get("/summary", response_model=LiquiditySummary)
def get_liquidity_summary(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> LiquiditySummary:
    """Portfolio liquidity by time bucket (independent of goals)."""
    return liquidity_summary(session, user_id)


@router.post("/refresh", response_model=RefreshResult)
def post_liquidity_refresh(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> RefreshResult:
    """Batch-update stored ``earliest_liquidity_date`` (run daily; callable manually)."""
    r = refresh_all_liquidity_dates(session, user_id)
    session.commit()
    return r


@router.get("/goal-match/{goal_id}", response_model=GoalHoldingMatch)
def get_goal_holding_match(
    goal_id: int,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> GoalHoldingMatch:
    """Holdings whose liquidity date is on or before the goal's ``target_date``."""
    try:
        return match_holdings_to_goal(session, goal_id, user_id)
    except ValueError as e:
        if str(e) == "goal_not_found":
            raise HTTPException(status_code=404, detail="Goal not found") from e
        raise


@router.get("/goal-suggestions", response_model=list[StartingBalanceSuggestion])
def get_goal_suggestions(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> list[StartingBalanceSuggestion]:
    """Per-goal accessible holdings + suggested starting balance (informational)."""
    return suggest_starting_balances(session, user_id)


@router.post("/mismatch-check", response_model=LiquidityMismatchResult)
def post_mismatch_check(
    body: MismatchCheckBody,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> LiquidityMismatchResult:
    """Compare claimed savings to holdings accessible before the goal date."""
    try:
        return check_liquidity_mismatch(
            session,
            body.goal_id,
            body.claimed_amount_inr,
            user_id,
        )
    except ValueError as e:
        if str(e) == "goal_not_found":
            raise HTTPException(status_code=404, detail="Goal not found") from e
        raise
