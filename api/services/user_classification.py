"""
Assemble :class:`pipeline.user_config.UserClassificationConfig` from SQLite.

Also seeds ``user_merchant_rules`` from ``data/merchant_starter_pack.json`` when
new keywords appear in a release (idempotent per user).
"""

from __future__ import annotations

import json
import logging
from typing import Iterable

from sqlmodel import Session, col, select

from api.models import UserClassificationSettings, UserContact, UserMerchantRule
from pipeline.models import CounterpartyCategory, TxnType
from pipeline.user_config import (
    CustomPattern,
    KnownContact,
    MerchantRule,
    MerchantRuleSource,
    UserClassificationConfig,
    load_merchant_starter_pack,
)

logger = logging.getLogger(__name__)


def _json_list(raw: str, default: list[str] | None = None) -> list[str]:
    if not raw or not raw.strip():
        return list(default or [])
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except json.JSONDecodeError:
        pass
    return list(default or [])


def merge_starter_pack_for_user(session: Session, user_id: str) -> int:
    """Insert missing STARTER_PACK merchant rows for *user_id*. Returns rows added."""
    starter = load_merchant_starter_pack()
    existing = session.exec(
        select(UserMerchantRule).where(UserMerchantRule.user_id == user_id)
    ).all()
    have = {r.keyword.upper() for r in existing}
    added = 0
    for mr in starter:
        k = mr.keyword.upper()
        if k in have:
            continue
        session.add(
            UserMerchantRule(
                user_id=user_id,
                keyword=mr.keyword,
                display_name=mr.display_name,
                counterparty_category=mr.category.value,
                source="STARTER_PACK",
            )
        )
        have.add(k)
        added += 1
    return added


def merge_starter_pack_for_all_users() -> None:
    """Called from ``init_db`` — seeds starter keywords for every user seen in the DB."""
    from api.database import get_engine
    from api.models import Transaction

    engine = get_engine()
    user_ids: set[str] = set()
    with Session(engine) as session:
        stmt = (
            select(Transaction.user_id)
            .where(col(Transaction.user_id).is_not(None))
            .distinct()
        )
        for uid in session.exec(stmt).all():
            if uid and str(uid).strip():
                user_ids.add(str(uid))
        for row in session.exec(select(UserClassificationSettings.user_id)):
            if row:
                user_ids.add(row)
        if not user_ids:
            return
        total_added = 0
        for uid in sorted(user_ids):
            total_added += merge_starter_pack_for_user(session, uid)
        session.commit()
        if total_added:
            logger.info("Starter merchant pack: inserted %d new keyword rows", total_added)


def _contacts_to_known(rows: Iterable[UserContact]) -> list[KnownContact]:
    out: list[KnownContact] = []
    for row in rows:
        aliases = _json_list(row.aliases_json, [])
        out.append(KnownContact(display_name=row.display_name, aliases=aliases))
    return out


def _db_merchant_rules_to_pipeline(rows: list[UserMerchantRule]) -> list[MerchantRule]:
    """Prefer user-learned rows first (same keyword should not duplicate)."""
    rows_sorted = sorted(
        rows,
        key=lambda r: (0 if r.source in ("USER_CORRECTION", "MANUAL") else 1, r.keyword),
    )
    out: list[MerchantRule] = []
    seen: set[str] = set()
    for r in rows_sorted:
        k = r.keyword.upper()
        if k in seen:
            continue
        seen.add(k)
        src = (
            MerchantRuleSource.STARTER_PACK
            if r.source == "STARTER_PACK"
            else MerchantRuleSource.USER_DB
        )
        out.append(
            MerchantRule(
                keyword=r.keyword,
                display_name=r.display_name,
                category=CounterpartyCategory(r.counterparty_category),
                source=src,
            )
        )
    return out


def load_user_classification_config(session: Session, user_id: str) -> UserClassificationConfig:
    """Build the config object used by :func:`pipeline.rules_classifier.classify_rules`."""
    merge_starter_pack_for_user(session, user_id)
    session.flush()

    settings = session.exec(
        select(UserClassificationSettings).where(UserClassificationSettings.user_id == user_id)
    ).first()

    self_name = settings.self_name if settings else ""
    self_aliases = _json_list(settings.self_aliases_json if settings else "[]", [])
    rent_recipient = settings.rent_recipient if settings else None
    rent_pattern = settings.rent_pattern if settings else None
    salary_indicators = _json_list(
        settings.salary_indicators_json if settings else "",
        default=["PAYROLL"],
    )
    account_hints = _json_list(settings.account_hints_json if settings else "[]", [])

    custom_patterns: list[CustomPattern] = []
    raw_cp = settings.custom_patterns_json if settings else "[]"
    try:
        for obj in json.loads(raw_cp or "[]"):
            if not isinstance(obj, dict):
                continue
            sub = str(obj.get("substring", "")).strip()
            tt = str(obj.get("txn_type", "")).strip()
            if sub and tt:
                try:
                    custom_patterns.append(
                        CustomPattern(substring=sub.upper(), set_txn_type=TxnType(tt))
                    )
                except ValueError:
                    pass
    except json.JSONDecodeError:
        pass

    contacts = session.exec(select(UserContact).where(UserContact.user_id == user_id)).all()
    family: list[KnownContact] = []
    friends: list[KnownContact] = []
    acq: list[KnownContact] = []
    for c in contacts:
        rel = (c.relationship or "").upper()
        if rel == "SELF":
            self_aliases.append(c.display_name)
            self_aliases.extend(_json_list(c.aliases_json, []))
            continue
        bucket = {"FAMILY": family, "FRIEND": friends, "ACQUAINTANCE": acq}.get(rel)
        if bucket is None:
            continue
        bucket.append(
            KnownContact(display_name=c.display_name, aliases=_json_list(c.aliases_json, []))
        )

    mrows = session.exec(
        select(UserMerchantRule).where(UserMerchantRule.user_id == user_id)
    ).all()
    merchant_rules = _db_merchant_rules_to_pipeline(list(mrows))

    # If DB had no merchant rows at all, fall back to file-only (first boot edge case).
    if not merchant_rules:
        merchant_rules = load_merchant_starter_pack()

    return UserClassificationConfig(
        self_name=self_name.strip(),
        self_aliases=list(dict.fromkeys(a.strip() for a in self_aliases if a.strip())),
        account_id_hints=list(dict.fromkeys(account_hints)),
        family_contacts=family,
        friend_contacts=friends,
        acquaintance_contacts=acq,
        merchant_rules=merchant_rules,
        rent_recipient=rent_recipient.strip() if rent_recipient else None,
        rent_pattern=rent_pattern.strip() if rent_pattern else None,
        salary_indicators=salary_indicators or ["PAYROLL"],
        custom_patterns=custom_patterns,
    )


def pipeline_config_for_account_owner(session: Session, account_id: str) -> UserClassificationConfig:
    """Resolve pipeline ``user_id`` from account mapping and load config."""
    from api.services.account_user_map import user_id_for_account

    uid = user_id_for_account(account_id)
    return load_user_classification_config(session, uid)


def get_or_create_user_classification_settings(
    session: Session, user_id: str
) -> UserClassificationSettings:
    """Return the singleton settings row for ``user_id``, inserting if missing."""
    row = session.exec(
        select(UserClassificationSettings).where(UserClassificationSettings.user_id == user_id)
    ).first()
    if row:
        return row
    row = UserClassificationSettings(user_id=user_id)
    session.add(row)
    session.flush()
    return row
