"""
User settings API — reminders for recurring obligations (rent, CC due, etc.).

GET    /api/settings/reminders      — list for current user
POST   /api/settings/reminders      — create
PATCH  /api/settings/reminders/{id} — update
DELETE /api/settings/reminders/{id} — delete
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import get_session
from api.models import Reminder

router = APIRouter()


class ReminderCreate(BaseModel):
    name: str
    due_day_of_month: int = Field(ge=1, le=31)
    amount: float | None = None
    counterparty_category: str | None = None
    is_active: bool = True


class ReminderUpdate(BaseModel):
    name: str | None = None
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    amount: float | None = None
    counterparty_category: str | None = None
    is_active: bool | None = None


def _reminder_to_dict(r: Reminder) -> dict:
    return {
        "id": r.id,
        "user_id": r.user_id,
        "name": r.name,
        "due_day_of_month": r.due_day_of_month,
        "amount": r.amount,
        "counterparty_category": r.counterparty_category,
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/reminders")
def list_reminders(
    *,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> list[dict]:
    q = (
        select(Reminder)
        .where(Reminder.user_id == user)
        .order_by(col(Reminder.due_day_of_month), col(Reminder.name))
    )
    return [_reminder_to_dict(r) for r in session.exec(q).all()]


@router.post("/reminders", status_code=201)
def create_reminder(
    body: ReminderCreate,
    *,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> dict:
    r = Reminder(
        user_id=user,
        name=body.name,
        due_day_of_month=body.due_day_of_month,
        amount=body.amount,
        counterparty_category=body.counterparty_category,
        is_active=body.is_active,
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return _reminder_to_dict(r)


@router.patch("/reminders/{reminder_id}")
def update_reminder(
    reminder_id: int,
    body: ReminderUpdate,
    *,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> dict:
    r = session.get(Reminder, reminder_id)
    if not r or r.user_id != user:
        raise HTTPException(status_code=404, detail="Reminder not found")

    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    r.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(r)
    session.commit()
    session.refresh(r)
    return _reminder_to_dict(r)


@router.delete("/reminders/{reminder_id}", status_code=204)
def delete_reminder(
    reminder_id: int,
    *,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> None:
    r = session.get(Reminder, reminder_id)
    if not r or r.user_id != user:
        raise HTTPException(status_code=404, detail="Reminder not found")
    session.delete(r)
    session.commit()
