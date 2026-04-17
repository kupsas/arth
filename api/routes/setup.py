"""
First-run setup wizard — registration and completion flag (DESKTOP_PREREQS item 3).

Secrets (PDF passwords) live in :class:`api.models.UserSecrets`; bank senders in
:class:`api.models.ScraperBankSender` / :class:`api.models.ScraperAccountMapping`.
"""

from __future__ import annotations

import datetime
import logging

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api.auth import get_current_user
from api.database import get_session
from api.models import AppUser, UserSecrets

logger = logging.getLogger(__name__)

router = APIRouter()


class SetupStatusResponse(BaseModel):
    """Whether the app still needs onboarding."""

    needs_setup: bool
    has_users: bool
    setup_completed: bool


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SecretsUpdateRequest(BaseModel):
    """JSON keys should match env var names (e.g. HDFC_STATEMENT_PASSWORD)."""

    secrets_json: dict[str, str]


@router.get("/status", response_model=SetupStatusResponse)
def setup_status(session: Session = Depends(get_session)) -> SetupStatusResponse:
    """Public: is registration required, and has anyone finished setup?"""
    users = session.exec(select(AppUser)).all()
    if not users:
        return SetupStatusResponse(
            needs_setup=True,
            has_users=False,
            setup_completed=False,
        )
    completed = any(u.setup_completed_at is not None for u in users)
    return SetupStatusResponse(
        needs_setup=not completed,
        has_users=True,
        setup_completed=completed,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_first_user(
    body: RegisterRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Create the first local user. Returns 403 if an account already exists."""
    if session.exec(select(AppUser)).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A user already exists — use /api/auth/login",
        )
    pw_hash = bcrypt.hashpw(
        body.password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("ascii")
    session.add(
        AppUser(
            username=body.username.strip(),
            password_hash=pw_hash,
            setup_completed_at=None,
        )
    )
    session.commit()
    logger.info("Registered first app user %r", body.username)
    return {"username": body.username, "status": "created"}


@router.post("/complete")
def mark_setup_complete(
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    """Mark the logged-in user as having finished the wizard (banks + OAuth)."""
    user = session.exec(
        select(AppUser).where(AppUser.username == current_user)
    ).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found in app_users")
    user.setup_completed_at = datetime.datetime.now(datetime.UTC)
    session.add(user)
    session.commit()
    return {"setup_completed": True, "username": current_user}


@router.get("/secrets/meta")
def list_secret_keys(
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    """Return which env-style keys are stored for this user — **values are never exposed**."""
    import json

    row = session.exec(
        select(UserSecrets).where(UserSecrets.user_id == current_user)
    ).first()
    if not row or not row.secrets_json:
        return {"keys": [], "has_secrets": False}
    try:
        data = json.loads(row.secrets_json)
        keys = list(data.keys()) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        keys = []
    return {"keys": keys, "has_secrets": bool(keys)}


@router.post("/secrets")
def put_user_secrets(
    body: SecretsUpdateRequest,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    """Store encrypted PDF/API secrets for the session user (JSON of env-key → value)."""
    import json

    row = session.exec(
        select(UserSecrets).where(UserSecrets.user_id == current_user)
    ).first()
    payload = json.dumps(body.secrets_json)
    if row is None:
        row = UserSecrets(user_id=current_user, secrets_json=payload)
    else:
        row.secrets_json = payload
        row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return {"ok": True, "keys": list(body.secrets_json.keys())}
