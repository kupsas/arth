"""
Authentication utilities for the Arth API.

Local open-source builds run **without login**: every HTTP request is treated as the
single installation user (:data:`api.constants.DEFAULT_LOCAL_USER`). Session cookies
and signed WS tickets remain supported for backward compatibility with the dashboard,
but missing credentials never yield 401.

``AUTH_SECRET_KEY`` (optional) signs cookies/tickets; when unset, an ephemeral key is
used so tokens reset on each process restart.
"""

from __future__ import annotations

import hashlib
import logging
import os

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Query, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from api.constants import DEFAULT_LOCAL_USER

load_dotenv()

logger = logging.getLogger(__name__)

COOKIE_NAME = "arth_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days in seconds

_AUTH_SECRET_KEY: str = os.getenv("AUTH_SECRET_KEY", "").strip()
if not _AUTH_SECRET_KEY:
    import secrets

    _AUTH_SECRET_KEY = secrets.token_hex(32)
    logger.warning(
        "AUTH_SECRET_KEY is not set — using an ephemeral signing key. "
        "Session cookies and WS tickets reset on every API restart."
    )

_serializer = URLSafeTimedSerializer(_AUTH_SECRET_KEY)


def internal_agent_username() -> str:
    """Identity string used for in-process agent → API calls."""
    return DEFAULT_LOCAL_USER


def agent_internal_token() -> str:
    """Secret for trusted in-process agent calls (``X-Arth-Internal`` header).

    Set ``AGENT_INTERNAL_TOKEN`` in ``.env`` for explicit control. Otherwise derived
    from ``AUTH_SECRET_KEY`` so the API and agent agree on one machine.
    """
    env = os.getenv("AGENT_INTERNAL_TOKEN", "").strip()
    if env:
        return env
    return hashlib.sha256(
        f"{_AUTH_SECRET_KEY}:arth-agent-internal-v1".encode("utf-8")
    ).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    """Always succeed — local install has no remote identity provider."""
    _ = username, password
    return True


def create_session_token(username: str) -> str:
    """Create a signed token; ``username`` is stored for WS ticket verification."""
    return _serializer.dumps(username)


def verify_session_token(token: str) -> str:
    """Verify token signature and expiry."""
    try:
        username: str = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return username
    except SignatureExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session expired — please sign in again.",
        )
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That sign-in link didn't work. Please sign in again.",
        )


def get_current_user() -> str:
    """Single-user local install — no credential gate."""
    return DEFAULT_LOCAL_USER


def effective_user_id(
    user_id: str | None = Query(
        None,
        description="Optional; when set must match the installation user.",
    ),
    current_user: str = Depends(get_current_user),
) -> str:
    """Resolve Arth user id for data-scoped endpoints."""
    out = (user_id or "").strip() or current_user
    if out != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That user id doesn't match who's signed in.",
        )
    return out
