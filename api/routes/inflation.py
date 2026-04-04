"""
Inflation rates API (Sub-Plan F).

GET  /api/inflation         — current rates + metadata
POST /api/inflation/refresh — force re-fetch from data.gov.in
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import get_current_user
from api.database import get_session
from api.services.inflation_service import all_current_rates_with_meta, fetch_and_cache_inflation
from sqlmodel import Session

router = APIRouter()


@router.get("")
def get_inflation(
    *,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
) -> dict:
    """Return merged inflation rates with source and freshness per category."""
    return all_current_rates_with_meta(session)


@router.post("/refresh")
def refresh_inflation(
    *,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
) -> dict:
    """Force a CPI fetch (if API key set) and return updated snapshot."""
    merged = fetch_and_cache_inflation(session)
    snap = all_current_rates_with_meta(session)
    snap["refreshed_headline"] = merged
    return snap
