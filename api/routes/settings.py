"""
User settings API — reminders for recurring obligations (rent, CC due, etc.).

GET    /api/settings/reminders                — list for current user
GET    /api/settings/reminders/status         — per-reminder match status for a month
POST   /api/settings/reminders/derive-anchors — preview auto-derived description anchors
POST   /api/settings/reminders                — create
PATCH  /api/settings/reminders/{id}           — update
DELETE /api/settings/reminders/{id}           — delete
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import get_session
from api.models import Reminder, Transaction
from api.reminder_anchor_derivation import (
    MAX_ANCHOR_LEN,
    MAX_DERIVED_ANCHORS,
    decode_description_match_anchors,
    derive_description_anchors,
    encode_description_match_anchors,
)
from api.reminder_matching import (
    MAX_EXAMPLE_TRANSACTION_IDS,
    compute_all_reminder_statuses,
    decode_example_transaction_ids,
    encode_example_transaction_ids,
    month_date_range,
)

router = APIRouter()


class ReminderCreate(BaseModel):
    name: str
    due_day_of_month: int = Field(ge=1, le=31)
    amount: float | None = None
    counterparty_category: str | None = None
    example_transaction_ids: list[int] | None = None
    description_match_anchors: list[str] | None = None
    is_active: bool = True


class ReminderUpdate(BaseModel):
    name: str | None = None
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    amount: float | None = None
    counterparty_category: str | None = None
    example_transaction_ids: list[int] | None = None
    description_match_anchors: list[str] | None = None
    is_active: bool | None = None


class DeriveAnchorsBody(BaseModel):
    transaction_ids: list[int]


def _examples_stale(session: Session, r: Reminder) -> bool:
    for tid in decode_example_transaction_ids(r.example_transaction_ids):
        if session.get(Transaction, tid) is None:
            return True
    return False


def _normalize_description_match_anchors(raw: list[str] | None) -> str | None:
    """Validate and JSON-encode for DB; None = caller means omit column update."""
    if raw is None:
        return None
    cleaned: list[str] = []
    for a in raw:
        s = (a or "").strip()
        if not s:
            continue
        if len(s) > MAX_ANCHOR_LEN:
            raise HTTPException(
                status_code=400,
                detail=f"Each anchor must be at most {MAX_ANCHOR_LEN} characters",
            )
        cleaned.append(s)
        if len(cleaned) > MAX_DERIVED_ANCHORS:
            raise HTTPException(
                status_code=400,
                detail=f"At most {MAX_DERIVED_ANCHORS} description match anchors",
            )
    return encode_description_match_anchors(cleaned) if cleaned else None


def _reminder_to_dict(session: Session, r: Reminder) -> dict:
    ex_ids = decode_example_transaction_ids(r.example_transaction_ids)
    anchors = decode_description_match_anchors(r.description_match_anchors)
    return {
        "id": r.id,
        "user_id": r.user_id,
        "name": r.name,
        "due_day_of_month": r.due_day_of_month,
        "amount": r.amount,
        "counterparty_category": r.counterparty_category,
        "example_transaction_ids": ex_ids,
        "description_match_anchors": anchors,
        "suggest_manual_anchors": bool(ex_ids) and not anchors,
        "examples_stale": _examples_stale(session, r),
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _normalize_example_ids(session: Session, raw: list[int] | None) -> str | None:
    """Validate IDs, return encoded JSON for DB column (NULL = no mapping)."""
    if raw is None:
        return None
    seen: set[int] = set()
    ids: list[int] = []
    for x in raw:
        if x not in seen:
            seen.add(x)
            ids.append(x)
    if len(ids) > MAX_EXAMPLE_TRANSACTION_IDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"At most {MAX_EXAMPLE_TRANSACTION_IDS} example "
                "transactions per reminder"
            ),
        )
    if not ids:
        return None
    for tid in ids:
        t = session.get(Transaction, tid)
        if t is None:
            raise HTTPException(
                status_code=400,
                detail=f"Transaction {tid} not found",
            )
        if t.direction != "OUTFLOW":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Transaction {tid} must be an expense (OUTFLOW) "
                    "to use as a payment example"
                ),
            )
        if not (t.counterparty or "").strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Transaction {tid} must have a counterparty "
                    "so reminders can match future payments"
                ),
            )
    return encode_example_transaction_ids(ids)


def _load_example_rows(session: Session, enc_ids: str | None) -> list[Transaction]:
    rows: list[Transaction] = []
    for tid in decode_example_transaction_ids(enc_ids):
        t = session.get(Transaction, tid)
        if t is not None:
            rows.append(t)
    return rows


@router.post("/reminders/derive-anchors")
def derive_anchors_preview(
    body: DeriveAnchorsBody,
    *,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> dict:
    """Suggest description/ref substrings from example transaction IDs (confirm in UI)."""
    _ = user  # auth only; txns are household-wide
    seen: set[int] = set()
    ids: list[int] = []
    for x in body.transaction_ids:
        if x not in seen:
            seen.add(x)
            ids.append(x)
    if len(ids) > MAX_EXAMPLE_TRANSACTION_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EXAMPLE_TRANSACTION_IDS} transaction IDs",
        )
    if not ids:
        return {"anchors": [], "ok": False}
    rows: list[Transaction] = []
    for tid in ids:
        t = session.get(Transaction, tid)
        if t is None:
            raise HTTPException(status_code=400, detail=f"Transaction {tid} not found")
        if t.direction != "OUTFLOW":
            raise HTTPException(
                status_code=400,
                detail=f"Transaction {tid} must be OUTFLOW",
            )
        rows.append(t)
    anchors = derive_description_anchors(rows)
    return {"anchors": anchors, "ok": bool(anchors)}


@router.get("/reminders/status")
def reminders_status(
    month: str = Query(
        ...,
        description="Calendar month YYYY-MM",
        pattern=r"^\d{4}-\d{2}$",
    ),
    active_only: bool = True,
    *,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> dict:
    """Match status for each reminder in the given month (dashboard use)."""
    try:
        month_date_range(month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    items = compute_all_reminder_statuses(
        session, user, month, active_only=active_only
    )
    return {"month": month, "items": items}


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
    return [_reminder_to_dict(session, r) for r in session.exec(q).all()]


@router.post("/reminders", status_code=201)
def create_reminder(
    body: ReminderCreate,
    *,
    session: Session = Depends(get_session),
    user: str = Depends(get_current_user),
) -> dict:
    enc = _normalize_example_ids(session, body.example_transaction_ids)
    example_rows = _load_example_rows(session, enc)

    if "description_match_anchors" in body.model_fields_set:
        anch_enc = _normalize_description_match_anchors(body.description_match_anchors)
    elif example_rows:
        derived = derive_description_anchors(example_rows)
        anch_enc = encode_description_match_anchors(derived) if derived else None
    else:
        anch_enc = None

    r = Reminder(
        user_id=user,
        name=body.name,
        due_day_of_month=body.due_day_of_month,
        amount=body.amount,
        counterparty_category=body.counterparty_category,
        example_transaction_ids=enc,
        description_match_anchors=anch_enc,
        is_active=body.is_active,
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return _reminder_to_dict(session, r)


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
    examples_updated = False
    if "example_transaction_ids" in data:
        enc = _normalize_example_ids(session, data.pop("example_transaction_ids"))
        r.example_transaction_ids = enc
        examples_updated = True

    if "description_match_anchors" in data:
        r.description_match_anchors = _normalize_description_match_anchors(
            data.pop("description_match_anchors")
        )
    elif examples_updated:
        rows = _load_example_rows(session, r.example_transaction_ids)
        derived = derive_description_anchors(rows) if rows else []
        r.description_match_anchors = (
            encode_description_match_anchors(derived) if derived else None
        )

    for k, v in data.items():
        setattr(r, k, v)
    r.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(r)
    session.commit()
    session.refresh(r)
    return _reminder_to_dict(session, r)


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
