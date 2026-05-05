"""
User classification settings: contacts, merchant rules, self-aliases, rent/payroll prefs.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api.auth import get_current_user
from api.database import get_session
from api.models import (
    FamilyMember,
    UserClassificationSettings,
    UserContact,
    UserMerchantRule,
)
from api.services.user_classification import (
    get_or_create_user_classification_settings,
    load_user_classification_config,
    merge_starter_pack_for_user,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────


class ContactCreate(BaseModel):
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    relationship: str = Field(pattern=r"^(SELF|FAMILY|FRIEND|ACQUAINTANCE)$")


class ContactUpdate(BaseModel):
    display_name: str | None = None
    aliases: list[str] | None = None
    relationship: str | None = None


class ClassificationSettingsPatch(BaseModel):
    self_name: str | None = None
    self_aliases: list[str] | None = None
    rent_recipient: str | None = None
    rent_pattern: str | None = None
    salary_indicators: list[str] | None = None
    custom_patterns: list[dict[str, Any]] | None = None
    account_hints: list[str] | None = None


class MerchantRuleCreate(BaseModel):
    keyword: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    counterparty_category: str = Field(min_length=1)


class MerchantRuleUpdate(BaseModel):
    keyword: str | None = None
    display_name: str | None = None
    counterparty_category: str | None = None


class FamilyMemberCreate(BaseModel):
    """Household member for **account ownership** (not UPI / classification contacts)."""

    name: str = Field(min_length=1, max_length=128)
    relationship: str = Field(min_length=1, max_length=64)


class FamilyMemberUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    relationship: str | None = Field(default=None, max_length=64)


# ── Contacts ────────────────────────────────────────────────────────────────


@router.get("/contacts")
def list_contacts(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    rows = session.exec(
        select(UserContact).where(UserContact.user_id == current_user)
    ).all()
    return [
        {
            "id": r.id,
            "display_name": r.display_name,
            "aliases": json.loads(r.aliases_json or "[]"),
            "relationship": r.relationship,
            "contact_source": r.contact_source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


@router.post("/contacts")
def create_contact(
    body: ContactCreate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = UserContact(
        user_id=current_user,
        display_name=body.display_name.strip(),
        aliases_json=json.dumps(body.aliases),
        relationship=body.relationship.upper(),
        contact_source="USER",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    logger.info("People & merchants contact saved — id=%s", row.id)
    return {"id": row.id}


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: int,
    body: ContactUpdate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = session.get(UserContact, contact_id)
    if not row or row.user_id != current_user:
        raise HTTPException(status_code=404, detail="Contact not found")
    data = body.model_dump(exclude_unset=True)
    if "display_name" in data and data["display_name"] is not None:
        row.display_name = data["display_name"].strip()
    if "aliases" in data and data["aliases"] is not None:
        row.aliases_json = json.dumps(data["aliases"])
    if "relationship" in data and data["relationship"] is not None:
        rel = data["relationship"].upper()
        if rel not in ("SELF", "FAMILY", "FRIEND", "ACQUAINTANCE"):
            raise HTTPException(status_code=400, detail="Invalid relationship")
        row.relationship = rel
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return {"ok": True}


@router.delete("/contacts/{contact_id}")
def delete_contact(
    contact_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = session.get(UserContact, contact_id)
    if not row or row.user_id != current_user:
        raise HTTPException(status_code=404, detail="Contact not found")
    session.delete(row)
    session.commit()
    return {"ok": True}


# ── Family members (account ownership) ─────────────────────────────────────


@router.get("/family-members")
def list_family_members(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    from api.services.family_member_utils import get_or_create_self_member

    # Ensure the default Self row exists so the UI always has an anchor owner.
    get_or_create_self_member(session, current_user)
    session.commit()
    rows = session.exec(
        select(FamilyMember).where(FamilyMember.user_id == current_user)
    ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "relationship": r.relationship,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/family-members")
def create_family_member(
    body: FamilyMemberCreate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    rel = body.relationship.strip().upper()
    if rel == "SELF":
        raise HTTPException(
            status_code=400,
            detail="The synthetic Self owner already exists; rename it via PATCH instead.",
        )
    row = FamilyMember(
        user_id=current_user,
        name=body.name.strip(),
        relationship=rel,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id}


@router.patch("/family-members/{member_id}")
def update_family_member(
    member_id: int,
    body: FamilyMemberUpdate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = session.get(FamilyMember, member_id)
    if not row or row.user_id != current_user:
        raise HTTPException(status_code=404, detail="Family member not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = data["name"].strip()
    if "relationship" in data and data["relationship"] is not None:
        new_rel = data["relationship"].strip().upper()
        if row.relationship == "SELF" and new_rel != "SELF":
            raise HTTPException(
                status_code=400,
                detail="Cannot change relationship away from SELF for the default owner row.",
            )
        if new_rel == "SELF" and row.relationship != "SELF":
            raise HTTPException(status_code=400, detail="Cannot re-label another row as SELF.")
        row.relationship = new_rel
    session.add(row)
    session.commit()
    return {"ok": True}


@router.delete("/family-members/{member_id}")
def delete_family_member(
    member_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = session.get(FamilyMember, member_id)
    if not row or row.user_id != current_user:
        raise HTTPException(status_code=404, detail="Family member not found")
    if row.relationship == "SELF":
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default Self owner (used by bank account mappings).",
        )
    session.delete(row)
    session.commit()
    return {"ok": True}


# ── Settings ────────────────────────────────────────────────────────────────


def _get_or_create_settings(session: Session, user_id: str) -> UserClassificationSettings:
    return get_or_create_user_classification_settings(session, user_id)


@router.get("/settings")
def get_classification_settings(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = _get_or_create_settings(session, current_user)
    session.commit()
    return {
        "self_name": row.self_name,
        "self_aliases": json.loads(row.self_aliases_json or "[]"),
        "rent_recipient": row.rent_recipient,
        "rent_pattern": row.rent_pattern,
        "salary_indicators": json.loads(row.salary_indicators_json or '["PAYROLL"]'),
        "custom_patterns": json.loads(row.custom_patterns_json or "[]"),
        "account_hints": json.loads(row.account_hints_json or "[]"),
    }


@router.patch("/settings")
def patch_classification_settings(
    body: ClassificationSettingsPatch,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = _get_or_create_settings(session, current_user)
    data = body.model_dump(exclude_unset=True)
    if "self_name" in data:
        row.self_name = data["self_name"] or ""
    if "self_aliases" in data and data["self_aliases"] is not None:
        row.self_aliases_json = json.dumps(data["self_aliases"])
    if "rent_recipient" in data:
        row.rent_recipient = data["rent_recipient"]
    if "rent_pattern" in data:
        row.rent_pattern = data["rent_pattern"]
    if "salary_indicators" in data and data["salary_indicators"] is not None:
        row.salary_indicators_json = json.dumps(data["salary_indicators"])
    if "custom_patterns" in data and data["custom_patterns"] is not None:
        row.custom_patterns_json = json.dumps(data["custom_patterns"])
    if "account_hints" in data and data["account_hints"] is not None:
        row.account_hints_json = json.dumps(data["account_hints"])
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return {"ok": True}


# ── Merchant rules CRUD ─────────────────────────────────────────────────────


@router.get("/merchant-rules")
def list_merchant_rules(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    merge_starter_pack_for_user(session, current_user)
    session.commit()
    rows = session.exec(
        select(UserMerchantRule)
        .where(UserMerchantRule.user_id == current_user)
        .order_by(UserMerchantRule.keyword)
    ).all()
    return [
        {
            "id": r.id,
            "keyword": r.keyword,
            "display_name": r.display_name,
            "counterparty_category": r.counterparty_category,
            "source": r.source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/merchant-rules")
def create_merchant_rule(
    body: MerchantRuleCreate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    kw = body.keyword.strip().upper()
    existing = session.exec(
        select(UserMerchantRule).where(
            UserMerchantRule.user_id == current_user,
            UserMerchantRule.keyword == kw,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Keyword already exists for this user")
    row = UserMerchantRule(
        user_id=current_user,
        keyword=kw,
        display_name=body.display_name.strip(),
        counterparty_category=body.counterparty_category.strip(),
        source="MANUAL",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id}


@router.patch("/merchant-rules/{rule_id}")
def update_merchant_rule(
    rule_id: int,
    body: MerchantRuleUpdate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = session.get(UserMerchantRule, rule_id)
    if not row or row.user_id != current_user:
        raise HTTPException(status_code=404, detail="Rule not found")
    data = body.model_dump(exclude_unset=True)
    if "keyword" in data and data["keyword"] is not None:
        row.keyword = data["keyword"].strip().upper()
    if "display_name" in data and data["display_name"] is not None:
        row.display_name = data["display_name"].strip()
    if "counterparty_category" in data and data["counterparty_category"] is not None:
        row.counterparty_category = data["counterparty_category"].strip()
    session.add(row)
    session.commit()
    return {"ok": True}


@router.delete("/merchant-rules/{rule_id}")
def delete_merchant_rule(
    rule_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    row = session.get(UserMerchantRule, rule_id)
    if not row or row.user_id != current_user:
        raise HTTPException(status_code=404, detail="Rule not found")
    session.delete(row)
    session.commit()
    return {"ok": True}


# ── Assembled config (debug / pipeline parity) ──────────────────────────────


@router.get("/classification-config")
def get_classification_config_dump(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    cfg = load_user_classification_config(session, current_user)
    return {
        "self_name": cfg.self_name,
        "self_aliases": cfg.self_aliases,
        "account_id_hints": cfg.account_id_hints,
        "merchant_rule_count": len(cfg.merchant_rules),
        "family_contacts": [c.display_name for c in cfg.family_contacts],
        "rent_recipient": cfg.rent_recipient,
        "salary_indicators": cfg.salary_indicators,
        "custom_patterns": [
            {"substring": p.substring, "txn_type": p.set_txn_type.value}
            for p in cfg.custom_patterns
        ],
    }
