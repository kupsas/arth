"""
Inflation rates API (Sub-Plan F).

GET  /api/inflation         — current rates + metadata
GET  /api/inflation/history  — monthly India CPI YoY series (newest first)
POST /api/inflation/refresh — full IMF history sync + snapshot
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from api.auth import get_current_user
from api.database import get_session
from api.services.inflation_service import (
    all_current_rates_with_meta,
    list_cpi_general_monthly_history_payload,
    merge_rates_from_db,
    sync_imf_cpi_history,
)
from sqlmodel import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
def get_inflation(
    *,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
) -> dict:
    """Return merged inflation rates with source and freshness per category."""
    return all_current_rates_with_meta(session)


@router.get("/history")
def get_inflation_history(
    limit: int = Query(240, ge=1, le=600, description="Max months to return (newest first)"),
    *,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
) -> dict:
    """Stored monthly YoY % for India CPI (all items), one row per ``YYYY-MM``."""
    return list_cpi_general_monthly_history_payload(session, limit=limit)


@router.post("/refresh")
def refresh_inflation(
    *,
    session: Session = Depends(get_session),
    _user: str = Depends(get_current_user),
) -> dict:
    """Run full IMF monthly history sync (no API key) and return snapshot + sync summary."""
    summary = sync_imf_cpi_history(session)
    logger.debug("Inflation refresh — sync summary=%s", summary)
    snap = all_current_rates_with_meta(session)
    snap["sync"] = summary
    snap["refreshed_headline"] = merge_rates_from_db(session)
    return snap
