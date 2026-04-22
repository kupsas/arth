"""
Onboarding wizard API (Track 2).

State endpoints are fully wired to :class:`~api.models.OnboardingState`.
Discovery, backfill, classification batches, and gap analysis are **stubs** until
later phases implement the orchestrator (see project plan).
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import get_current_user
from api.database import get_session
from api.models import AppUser, OnboardingState

router = APIRouter()


def _parse_json_object(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except json.JSONDecodeError:
        return default


def _get_or_create_state(session: Session, user_id: str) -> OnboardingState:
    row = session.exec(select(OnboardingState).where(OnboardingState.user_id == user_id)).first()
    if row:
        return row
    row = OnboardingState(user_id=user_id)
    session.add(row)
    session.flush()
    return row


class OnboardingStateResponse(BaseModel):
    """Serializable wizard snapshot for the dashboard."""

    current_step: str
    completed_steps: list[Any]
    discovery_results: dict[str, Any]
    backfill_progress: dict[str, Any]
    created_at: str | None
    updated_at: str | None


class OnboardingStatePatch(BaseModel):
    """Partial update from the client (e.g. step change after completing a screen)."""

    current_step: str | None = None
    completed_steps: list[Any] | None = None
    discovery_results: dict[str, Any] | None = None
    backfill_progress: dict[str, Any] | None = None


@router.get("/state", response_model=OnboardingStateResponse)
def get_onboarding_state(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> OnboardingStateResponse:
    row = _get_or_create_state(session, current_user)
    session.commit()
    return OnboardingStateResponse(
        current_step=row.current_step,
        completed_steps=_parse_json_object(row.completed_steps_json, []),
        discovery_results=_parse_json_object(row.discovery_results_json, {}),
        backfill_progress=_parse_json_object(row.backfill_progress_json, {}),
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.patch("/state", response_model=OnboardingStateResponse)
def patch_onboarding_state(
    body: OnboardingStatePatch,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> OnboardingStateResponse:
    row = _get_or_create_state(session, current_user)
    data = body.model_dump(exclude_unset=True)
    if "current_step" in data and data["current_step"] is not None:
        row.current_step = data["current_step"].strip() or row.current_step
    if "completed_steps" in data and data["completed_steps"] is not None:
        row.completed_steps_json = json.dumps(data["completed_steps"])
    if "discovery_results" in data and data["discovery_results"] is not None:
        row.discovery_results_json = json.dumps(data["discovery_results"])
    if "backfill_progress" in data and data["backfill_progress"] is not None:
        row.backfill_progress_json = json.dumps(data["backfill_progress"])
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return OnboardingStateResponse(
        current_step=row.current_step,
        completed_steps=_parse_json_object(row.completed_steps_json, []),
        discovery_results=_parse_json_object(row.discovery_results_json, {}),
        backfill_progress=_parse_json_object(row.backfill_progress_json, {}),
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.post("/discover")
def onboarding_discover(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Placeholder — Phase 2 wires Gmail discovery here."""
    _ = session, current_user
    return {
        "status": "not_implemented",
        "sources": [],
        "message": "Discovery engine lands in phase 2 (scraper.discovery).",
    }


@router.post("/backfill/{source}")
def onboarding_backfill(
    source: str,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Placeholder — Phase 2 adds the backfill orchestrator."""
    _ = session, current_user
    return {
        "status": "not_implemented",
        "source": source,
        "message": "Backfill orchestrator lands in phase 2.",
    }


@router.get("/backfill/{source}/progress")
def onboarding_backfill_progress(
    source: str,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    _ = session, current_user
    return {"source": source, "status": "idle", "percent": 0.0}


@router.post("/classify")
def onboarding_classify(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    _ = session, current_user
    return {"status": "not_implemented", "processed": 0}


@router.get("/gaps")
def onboarding_gaps(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    _ = session, current_user
    return {"gaps": []}


@router.post("/complete")
def onboarding_complete(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark wizard finished and align with first-run ``setup_completed`` when applicable."""
    row = _get_or_create_state(session, current_user)
    row.current_step = "completed"
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)

    user_row = session.exec(select(AppUser).where(AppUser.username == current_user)).first()
    if user_row and user_row.setup_completed_at is None:
        user_row.setup_completed_at = datetime.datetime.now(datetime.UTC)
        session.add(user_row)

    session.commit()
    return {"ok": True, "current_step": row.current_step}
