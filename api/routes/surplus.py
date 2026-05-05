"""
Surplus API — Sub-Plan B.

GET /api/surplus         — full surplus breakdown (rolling median, dual path)
GET /api/surplus/monthly — per-month rows only (for charts)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from api.auth import effective_user_id
from api.database import get_session
from api.services.surplus_calculator import MonthDetail, SurplusResult, compute_surplus

logger = logging.getLogger(__name__)

router = APIRouter()


class MonthlyOnlyResponse(BaseModel):
    """Chart-friendly slice of :class:`SurplusResult`."""

    user_id: str
    months_analyzed: int
    month_details: list[MonthDetail]


@router.get("", response_model=SurplusResult)
def get_surplus(
    months: int = Query(
        default=6,
        ge=3,
        le=12,
        description="Trailing calendar months for rolling median (3–12)",
    ),
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> SurplusResult:
    """Compute monthly surplus from recurring income + category-filtered spend."""
    result = compute_surplus(session, user_id, months)
    logger.debug(
        "Surplus API computed months=%s method=%s",
        months,
        result.computation_method,
    )
    return result


@router.get("/monthly", response_model=MonthlyOnlyResponse)
def get_surplus_monthly(
    months: int = Query(default=6, ge=3, le=12),
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
) -> MonthlyOnlyResponse:
    """Return only the per-month breakdown (lighter payload for charts)."""
    full = compute_surplus(session, user_id, months)
    return MonthlyOnlyResponse(
        user_id=full.user_id,
        months_analyzed=full.months_analyzed,
        month_details=full.month_details,
    )
