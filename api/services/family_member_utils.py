"""
Helpers for account ownership (FamilyMember).

``FamilyMember`` is separate from ``UserContact`` (classification / UPI matching).
Every user has a synthetic "Self" row used as the default ``member_id`` on
``ScraperAccountMapping`` so existing accounts behave as before onboarding.
"""

from __future__ import annotations

from sqlmodel import Session, select

from api.models import FamilyMember

# Default owner label seeded for each user (see database patches + scraper seed).
SELF_NAME = "Self"
SELF_RELATIONSHIP = "SELF"


def get_or_create_self_member(session: Session, user_id: str) -> FamilyMember:
    """Return the user's Self row, creating it if missing (idempotent)."""
    uid = (user_id or "").strip()
    row = session.exec(
        select(FamilyMember).where(
            FamilyMember.user_id == uid,
            FamilyMember.relationship == SELF_RELATIONSHIP,
        )
    ).first()
    if row:
        return row
    row = FamilyMember(
        user_id=uid,
        name=SELF_NAME,
        relationship=SELF_RELATIONSHIP,
    )
    session.add(row)
    session.flush()
    return row


def self_member_id(session: Session, user_id: str) -> int:
    """Primary key of the Self ``FamilyMember`` for ``user_id``."""
    return get_or_create_self_member(session, user_id).id  # type: ignore[return-value]
