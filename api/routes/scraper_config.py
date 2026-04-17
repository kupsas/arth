"""
CRUD for DB-backed Gmail scraper config (DESKTOP_PREREQS item 1 / wizard item 3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api.auth import get_current_user
from api.database import get_session
from api.models import ScraperAccountMapping, ScraperBankSender
from scraper.email_router import _normalise_sender

router = APIRouter()


class BankSenderIn(BaseModel):
    sender_email: str = Field(min_length=3)
    parser_key: str | None = None
    first_run_lookback_days: int | None = None
    enabled: bool = True


class AccountMappingIn(BaseModel):
    sender_email: str
    last_4_digits: str = Field(min_length=1, max_length=8)
    account_id: str
    source_key: str


@router.get("/senders")
def list_senders(
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    rows = session.exec(
        select(ScraperBankSender).where(ScraperBankSender.user_id == current_user)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "sender_email": r.sender_email,
                "parser_key": r.parser_key,
                "first_run_lookback_days": r.first_run_lookback_days,
                "enabled": r.enabled,
            }
            for r in rows
        ]
    }


@router.post("/senders")
def upsert_sender(
    body: BankSenderIn,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    key = _normalise_sender(body.sender_email)
    existing = session.exec(
        select(ScraperBankSender).where(
            ScraperBankSender.user_id == current_user,
            ScraperBankSender.sender_email == key,
        )
    ).first()
    if existing:
        existing.parser_key = body.parser_key
        existing.first_run_lookback_days = body.first_run_lookback_days
        existing.enabled = body.enabled
        session.add(existing)
    else:
        session.add(
            ScraperBankSender(
                user_id=current_user,
                sender_email=key,
                parser_key=body.parser_key,
                first_run_lookback_days=body.first_run_lookback_days,
                enabled=body.enabled,
            )
        )
    session.commit()
    return {"ok": True, "sender_email": key}


@router.get("/mappings")
def list_mappings(
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    rows = session.exec(
        select(ScraperAccountMapping).where(
            ScraperAccountMapping.user_id == current_user
        )
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "sender_email": r.sender_email,
                "last_4_digits": r.last_4_digits,
                "account_id": r.account_id,
                "source_key": r.source_key,
            }
            for r in rows
        ]
    }


@router.post("/mappings")
def upsert_mapping(
    body: AccountMappingIn,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    se = _normalise_sender(body.sender_email)
    existing = session.exec(
        select(ScraperAccountMapping).where(
            ScraperAccountMapping.user_id == current_user,
            ScraperAccountMapping.sender_email == se,
            ScraperAccountMapping.last_4_digits == body.last_4_digits,
        )
    ).first()
    if existing:
        existing.account_id = body.account_id
        existing.source_key = body.source_key
        session.add(existing)
    else:
        session.add(
            ScraperAccountMapping(
                user_id=current_user,
                sender_email=se,
                last_4_digits=body.last_4_digits,
                account_id=body.account_id,
                source_key=body.source_key,
            )
        )
    session.commit()
    return {"ok": True}


@router.delete("/mappings/{mapping_id}")
def delete_mapping(
    mapping_id: int,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    row = session.get(ScraperAccountMapping, mapping_id)
    if row is None or row.user_id != current_user:
        raise HTTPException(status_code=404, detail="Mapping not found")
    session.delete(row)
    session.commit()
    return {"ok": True}
