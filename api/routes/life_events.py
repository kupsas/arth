"""
LifeEvent CRUD — Phase B.3

Boolean milestones referenced by activation DSL ``event:<key>``. Marking
``occurred=True`` runs ``check_and_update_activations`` so PENDING goals can
flip to ACTIVE in the same transaction.
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import get_session
from api.models import LifeEvent
from api.services.activation_engine import check_and_update_activations

logger = logging.getLogger(__name__)

router = APIRouter()


class LifeEventCreate(BaseModel):
    event_key: str = Field(min_length=1, max_length=64)
    occurred: bool = False
    occurred_date: datetime.date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class LifeEventPatch(BaseModel):
    occurred: bool | None = None
    occurred_date: datetime.date | None = None
    notes: str | None = Field(default=None, max_length=2000)


def _event_to_dict(e: LifeEvent) -> dict:
    return {
        "id": e.id,
        "event_key": e.event_key,
        "occurred": e.occurred,
        "occurred_date": e.occurred_date.isoformat() if e.occurred_date else None,
        "user_id": e.user_id,
        "notes": e.notes,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


@router.get("")
def list_life_events(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> list[dict]:
    rows = session.exec(
        select(LifeEvent)
        .where(LifeEvent.user_id == current_user)
        .order_by(col(LifeEvent.event_key))
    ).all()
    return [_event_to_dict(r) for r in rows]


@router.post("", status_code=201)
def create_life_event(
    body: LifeEventCreate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    existing = session.exec(
        select(LifeEvent)
        .where(LifeEvent.user_id == current_user)
        .where(LifeEvent.event_key == body.event_key)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Life event {body.event_key!r} already exists for this user.",
        )

    row = LifeEvent(
        event_key=body.event_key,
        occurred=body.occurred,
        occurred_date=body.occurred_date,
        notes=body.notes,
        user_id=current_user,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    logger.info("Life event created id=%s", row.id)
    return _event_to_dict(row)


@router.patch("/{event_id}")
def patch_life_event(
    event_id: int,
    body: LifeEventPatch,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    row = session.get(LifeEvent, event_id)
    if not row or row.user_id != current_user:
        raise HTTPException(status_code=404, detail=f"Life event {event_id} not found")

    data = body.model_dump(exclude_unset=True)
    prev_occurred = row.occurred

    if "occurred" in data:
        row.occurred = data["occurred"]
    if "occurred_date" in data:
        row.occurred_date = data["occurred_date"]
    if "notes" in data:
        row.notes = data["notes"]

    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.flush()

    # When an event flips to True, dependent PENDING goals may become ACTIVE.
    if data.get("occurred") is True and not prev_occurred:
        check_and_update_activations(session, current_user)

    session.commit()
    session.refresh(row)
    logger.debug(
        "Life event patched id=%s activation_checked=%s",
        event_id,
        data.get("occurred") is True and not prev_occurred,
    )
    return _event_to_dict(row)
