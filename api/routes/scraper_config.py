"""
CRUD for DB-backed Gmail scraper config (DESKTOP_PREREQS item 1 / wizard item 3).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api.auth import get_current_user
from api.database import get_session
from api.models import FamilyMember, ScraperAccountMapping, ScraperBankSender
from api.services.family_member_utils import self_member_id
from scraper.email_router import _normalise_sender

router = APIRouter()


class BankSenderIn(BaseModel):
    sender_email: str = Field(min_length=3)
    parser_key: str | None = None
    first_run_lookback_days: int | None = None
    enabled: bool = True
    display_name: str | None = None
    source_type: str | None = None
    discovery_subject_patterns: list[str] | None = None
    expected_cadence: str | None = None


class AccountMappingIn(BaseModel):
    sender_email: str
    last_4_digits: str = Field(min_length=1, max_length=8)
    account_id: str
    source_key: str
    # Defaults to the user's synthetic "Self" owner when omitted.
    member_id: int | None = None


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
                "display_name": r.display_name,
                "source_type": r.source_type,
                "expected_cadence": r.expected_cadence,
                "discovery_subject_patterns": (
                    json.loads(r.discovery_subject_patterns_json)
                    if r.discovery_subject_patterns_json
                    else None
                ),
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
    pat_json = json.dumps(body.discovery_subject_patterns) if body.discovery_subject_patterns else None
    if existing:
        existing.parser_key = body.parser_key
        existing.first_run_lookback_days = body.first_run_lookback_days
        existing.enabled = body.enabled
        if body.display_name is not None:
            existing.display_name = body.display_name
        if body.source_type is not None:
            existing.source_type = body.source_type
        if body.expected_cadence is not None:
            existing.expected_cadence = body.expected_cadence
        if body.discovery_subject_patterns is not None:
            existing.discovery_subject_patterns_json = pat_json
        session.add(existing)
    else:
        session.add(
            ScraperBankSender(
                user_id=current_user,
                sender_email=key,
                parser_key=body.parser_key,
                first_run_lookback_days=body.first_run_lookback_days,
                enabled=body.enabled,
                display_name=body.display_name,
                source_type=body.source_type,
                expected_cadence=body.expected_cadence,
                discovery_subject_patterns_json=pat_json,
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
                "member_id": r.member_id,
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
    mid = body.member_id
    if mid is not None:
        owner = session.get(FamilyMember, mid)
        if owner is None or owner.user_id != current_user:
            raise HTTPException(status_code=400, detail="Invalid member_id for this user")
    else:
        mid = self_member_id(session, current_user)

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
        existing.member_id = mid
        session.add(existing)
    else:
        session.add(
            ScraperAccountMapping(
                user_id=current_user,
                sender_email=se,
                last_4_digits=body.last_4_digits,
                account_id=body.account_id,
                source_key=body.source_key,
                member_id=mid,
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
