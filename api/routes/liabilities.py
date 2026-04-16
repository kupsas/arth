"""
Liabilities API — Phase A.3.3

CRUD plus debt summary (ties into ``api.services.net_worth.liability_summary``).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from api.auth import effective_user_id, get_current_user
from api.database import get_session
from api.models import Liability
from api.services.net_worth import liability_summary
from pipeline.models import LiabilityType

router = APIRouter()

_VALID_LIAB = {e.value for e in LiabilityType}


class LiabilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None
    name: str
    liability_type: str
    principal_outstanding: float
    interest_rate: float
    emi_amount: float | None
    tenure_remaining_months: int | None
    emi_start_date: datetime.date | None
    emi_end_date: datetime.date | None
    user_id: str
    is_active: bool
    notes: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class LiabilitySummaryOut(BaseModel):
    """Totals from ``liability_summary`` plus row count."""

    principal_outstanding: float
    monthly_emi_burden: float
    debt_to_asset_ratio: float
    active_count: int


class LiabilityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    liability_type: str
    principal_outstanding: float = Field(gt=0)
    interest_rate: float = Field(ge=0, le=100)
    emi_amount: float | None = Field(default=None, ge=0)
    tenure_remaining_months: int | None = Field(default=None, ge=0)
    emi_start_date: datetime.date | None = None
    emi_end_date: datetime.date | None = None
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=10_000)


class LiabilityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    liability_type: str | None = None
    principal_outstanding: float | None = Field(default=None, gt=0)
    interest_rate: float | None = Field(default=None, ge=0, le=100)
    emi_amount: float | None = Field(default=None, ge=0)
    tenure_remaining_months: int | None = Field(default=None, ge=0)
    emi_start_date: datetime.date | None = None
    emi_end_date: datetime.date | None = None
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=10_000)


@router.get("", response_model=list[LiabilityOut])
def list_liabilities(
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
    is_active: bool | None = None,
):
    q = select(Liability).where(Liability.user_id == user_id)
    if is_active is not None:
        q = q.where(Liability.is_active == is_active)
    q = q.order_by(Liability.name)
    return list(session.exec(q).all())


@router.get("/summary", response_model=LiabilitySummaryOut)
def liabilities_summary(
    *, session: Session = Depends(get_session), user_id: str = Depends(effective_user_id)
):
    rows = list(
        session.exec(
            select(Liability).where(
                Liability.user_id == user_id,
                Liability.is_active == True,  # noqa: E712
            )
        ).all()
    )
    s = liability_summary(session, user_id=user_id)
    return LiabilitySummaryOut(
        principal_outstanding=s["principal_outstanding"],
        monthly_emi_burden=s["monthly_emi_burden"],
        debt_to_asset_ratio=s["debt_to_asset_ratio"],
        active_count=len(rows),
    )


@router.get("/{liability_id}", response_model=LiabilityOut)
def get_liability(
    liability_id: int,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
):
    row = session.get(Liability, liability_id)
    if not row or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Liability not found")
    return row


@router.post("/", response_model=LiabilityOut, status_code=201)
def create_liability(
    body: LiabilityCreate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    if body.liability_type not in _VALID_LIAB:
        raise HTTPException(status_code=400, detail=f"Invalid liability_type: {body.liability_type!r}")
    today = datetime.datetime.now(datetime.UTC).date()
    for dname, dval in (("emi_start_date", body.emi_start_date), ("emi_end_date", body.emi_end_date)):
        if dval is not None and dval > today:
            raise HTTPException(status_code=400, detail=f"{dname} cannot be in the future")
    li = Liability(
        name=body.name.strip(),
        liability_type=body.liability_type,
        principal_outstanding=body.principal_outstanding,
        interest_rate=body.interest_rate,
        emi_amount=body.emi_amount,
        tenure_remaining_months=body.tenure_remaining_months,
        emi_start_date=body.emi_start_date,
        emi_end_date=body.emi_end_date,
        user_id=current_user,
        is_active=body.is_active,
        notes=body.notes,
    )
    session.add(li)
    session.commit()
    session.refresh(li)
    return li


@router.patch("/{liability_id}", response_model=LiabilityOut)
def patch_liability(
    liability_id: int,
    body: LiabilityUpdate,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
):
    row = session.get(Liability, liability_id)
    if not row or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Liability not found")
    data = body.model_dump(exclude_unset=True)
    if "liability_type" in data and data["liability_type"] not in _VALID_LIAB:
        raise HTTPException(status_code=400, detail=f"Invalid liability_type: {data['liability_type']!r}")
    today = datetime.datetime.now(datetime.UTC).date()
    for key in ("emi_start_date", "emi_end_date"):
        if key in data and data[key] is not None and data[key] > today:
            raise HTTPException(status_code=400, detail=f"{key} cannot be in the future")
    for k, v in data.items():
        setattr(row, k, v)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{liability_id}", status_code=204)
def delete_liability(
    liability_id: int,
    *,
    session: Session = Depends(get_session),
    user_id: str = Depends(effective_user_id),
):
    row = session.get(Liability, liability_id)
    if not row or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Liability not found")
    session.delete(row)
    session.commit()
    return None
