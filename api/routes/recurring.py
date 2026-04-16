"""
Recurring Pattern endpoints — Phase 4.5c

POST /api/recurring/detect      — run detection, return counts
GET  /api/recurring             — list patterns (filterable)
GET  /api/recurring/summary     — total fixed costs + recurring income
GET  /api/recurring/{id}        — single pattern with linked transactions
PATCH /api/recurring/{id}       — confirm/dismiss/adjust a pattern
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from api.auth import effective_user_id, get_current_user
from api.database import get_session
from api.models import RecurringPattern, Transaction
from api.services import recurring_detector

logger = logging.getLogger(__name__)
router = APIRouter()


# ───────────────────────────────────────────────────────────────────────────
# Response schemas
# ───────────────────────────────────────────────────────────────────────────

class RecurringPatternOut(BaseModel):
    id: int
    user_id: str
    counterparty: str
    counterparty_category: str | None
    direction: str
    expected_amount: float
    amount_tolerance: float
    frequency: str
    day_of_month: int | None
    last_seen_date: str          # ISO date string
    next_expected_date: str | None
    is_active: bool
    is_confirmed: bool
    match_count: int
    total_amount: float
    created_at: str
    updated_at: str


class RecurringSummary(BaseModel):
    total_monthly_fixed_cost: float    # sum of active MONTHLY/WEEKLY OUTFLOW patterns
    total_monthly_recurring_income: float  # sum of active MONTHLY INFLOW patterns
    active_pattern_count: int
    patterns_due_this_week: int        # next_expected_date within 7 days


class PatternUpdate(BaseModel):
    is_confirmed: bool | None = None
    is_active: bool | None = None
    expected_amount: float | None = None
    frequency: str | None = None


# ───────────────────────────────────────────────────────────────────────────
# POST /detect — run the detection algorithm
# ───────────────────────────────────────────────────────────────────────────

@router.post("/detect")
def run_detection(*, session: Session = Depends(get_session)) -> dict:
    """Run recurring transaction detection on all transactions.

    This scans every (counterparty, direction) group looking for consistent
    intervals (std dev < 25% of median). Upserts recurring_patterns rows.

    Safe to call multiple times — fully idempotent.
    """
    result = recurring_detector.detect_and_upsert(session)
    return {
        "message": "Detection complete",
        **result,
    }


# ───────────────────────────────────────────────────────────────────────────
# GET /summary — aggregate stats for the dashboard card
# ───────────────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=RecurringSummary)
def get_recurring_summary(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> RecurringSummary:
    """Return aggregate stats about recurring patterns.

    Used by the dashboard's "Recurring" card to show total fixed monthly
    costs and total recurring income at a glance.
    """
    active_patterns = session.exec(
        select(RecurringPattern).where(
            RecurringPattern.user_id == user_id,
            RecurringPattern.is_active == True,  # noqa: E712
        )
    ).all()

    today = datetime.date.today()
    in_7_days = today + datetime.timedelta(days=7)

    monthly_cost = 0.0
    monthly_income = 0.0
    due_this_week = 0

    for p in active_patterns:
        # Normalise everything to a monthly equivalent amount for comparison
        multiplier = {"WEEKLY": 4.33, "MONTHLY": 1.0, "QUARTERLY": 1 / 3, "YEARLY": 1 / 12}
        factor = multiplier.get(p.frequency, 1.0)

        if p.direction == "OUTFLOW":
            monthly_cost += p.expected_amount * factor
        else:
            monthly_income += p.expected_amount * factor

        if p.next_expected_date and today <= p.next_expected_date <= in_7_days:
            due_this_week += 1

    return RecurringSummary(
        total_monthly_fixed_cost=round(monthly_cost, 2),
        total_monthly_recurring_income=round(monthly_income, 2),
        active_pattern_count=len(active_patterns),
        patterns_due_this_week=due_this_week,
    )


# ───────────────────────────────────────────────────────────────────────────
# GET / — list patterns
# ───────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[RecurringPatternOut])
def list_patterns(
    direction: str | None = Query(None, description="INFLOW or OUTFLOW"),
    frequency: str | None = Query(None, description="WEEKLY, MONTHLY, QUARTERLY, YEARLY"),
    is_active: bool | None = Query(None),
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> list[RecurringPatternOut]:
    """List recurring patterns with optional filters."""
    query = select(RecurringPattern).where(RecurringPattern.user_id == user_id)

    if direction is not None:
        query = query.where(RecurringPattern.direction == direction)
    if frequency is not None:
        query = query.where(RecurringPattern.frequency == frequency)
    if is_active is not None:
        query = query.where(RecurringPattern.is_active == is_active)

    query = query.order_by(
        col(RecurringPattern.expected_amount).desc()
    )

    patterns = session.exec(query).all()
    return [_pattern_out(p) for p in patterns]


# ───────────────────────────────────────────────────────────────────────────
# GET /{id} — single pattern with linked transactions
# ───────────────────────────────────────────────────────────────────────────

@router.get("/{pattern_id}")
def get_pattern(
    pattern_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    """Get a single recurring pattern and the transactions that match it."""
    pattern = session.get(RecurringPattern, pattern_id)
    if not pattern or pattern.user_id != current_user:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")

    # Find transactions that match this pattern (same counterparty + direction)
    linked_txns = session.exec(
        select(Transaction)
        .where(Transaction.user_id == pattern.user_id)
        .where(Transaction.counterparty == pattern.counterparty)
        .where(Transaction.direction == pattern.direction)
        .order_by(col(Transaction.txn_date).desc())
        .limit(50)
    ).all()

    return {
        **_pattern_out(pattern).model_dump(),
        "transactions": [
            {
                "id": t.id,
                "txn_date": t.txn_date.isoformat(),
                "amount": t.amount,
                "raw_description": t.raw_description,
            }
            for t in linked_txns
        ],
    }


# ───────────────────────────────────────────────────────────────────────────
# PATCH /{id} — update a pattern (confirm, dismiss, adjust)
# ───────────────────────────────────────────────────────────────────────────

@router.patch("/{pattern_id}", response_model=RecurringPatternOut)
def update_pattern(
    pattern_id: int,
    body: PatternUpdate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> RecurringPatternOut:
    """Confirm, dismiss, or adjust a recurring pattern."""
    pattern = session.get(RecurringPattern, pattern_id)
    if not pattern or pattern.user_id != current_user:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(pattern, field, value)

    pattern.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(pattern)
    session.commit()
    session.refresh(pattern)
    return _pattern_out(pattern)


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _pattern_out(p: RecurringPattern) -> RecurringPatternOut:
    return RecurringPatternOut(
        id=p.id or 0,
        user_id=p.user_id or "",
        counterparty=p.counterparty,
        counterparty_category=p.counterparty_category,
        direction=p.direction,
        expected_amount=p.expected_amount,
        amount_tolerance=p.amount_tolerance,
        frequency=p.frequency,
        day_of_month=p.day_of_month,
        last_seen_date=p.last_seen_date.isoformat(),
        next_expected_date=p.next_expected_date.isoformat() if p.next_expected_date else None,
        is_active=p.is_active,
        is_confirmed=p.is_confirmed,
        match_count=p.match_count,
        total_amount=p.total_amount,
        created_at=p.created_at.isoformat() if p.created_at else "",
        updated_at=p.updated_at.isoformat() if p.updated_at else "",
    )
