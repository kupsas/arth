"""
Authentication utilities for the Arth API.

Strategy: cookie-based sessions, single shared password.

Flow:
  1. POST /api/auth/login  → verify username + bcrypt password
                           → create a signed token (itsdangerous)
                           → set it as an httpOnly cookie named "arth_session"
  2. Every protected endpoint uses Depends(get_current_user)
     → reads "arth_session" cookie → verifies signature + expiry
     → returns the username, or raises HTTP 401
  3. POST /api/auth/logout → clears the cookie

Password storage:
  AUTH_PASSWORD is loaded from .env as plaintext, then hashed with bcrypt once
  at module import time.  The hash lives in memory only — never written to disk.
  This means the password can be changed by editing .env and restarting.

Session tokens:
  itsdangerous.URLSafeTimedSerializer signs a JSON payload with AUTH_SECRET_KEY.
  Tokens expire after SESSION_MAX_AGE seconds (default: 30 days).
  They are NOT JWT — they can't be inspected without the secret key.
"""

from __future__ import annotations

import hashlib
import logging
import os

import bcrypt
from dotenv import load_dotenv
from fastapi import Cookie, Depends, Header, HTTPException, Query, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read once at startup
# ---------------------------------------------------------------------------

COOKIE_NAME = "arth_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days in seconds

_AUTH_USERNAME: str = os.getenv("AUTH_USERNAME", "sashank")
_AUTH_PASSWORD_PLAIN: str = os.getenv("AUTH_PASSWORD", "")
_AUTH_SECRET_KEY: str = os.getenv("AUTH_SECRET_KEY", "")

if not _AUTH_PASSWORD_PLAIN:
    logger.warning("AUTH_PASSWORD is not set in .env — login will always fail")

if not _AUTH_SECRET_KEY:
    logger.warning(
        "AUTH_SECRET_KEY is not set in .env — using a random key. "
        "All sessions will be invalidated on every restart."
    )
    import secrets
    _AUTH_SECRET_KEY = secrets.token_hex(32)

# Hash the password once at module load time (bcrypt is slow by design — ~100ms).
# We never store the hash anywhere; it lives in memory for the lifetime of the process.
_PASSWORD_HASH: bytes = bcrypt.hashpw(
    _AUTH_PASSWORD_PLAIN.encode("utf-8"),
    bcrypt.gensalt(rounds=12),
)

# The serializer signs/verifies tokens using the secret key.
_serializer = URLSafeTimedSerializer(_AUTH_SECRET_KEY)


def internal_agent_username() -> str:
    """Username returned for the in-process agent (``X-Arth-Internal`` header).

    Matches ``get_current_user`` when the internal secret is present — i.e. the
    configured ``AUTH_USERNAME`` (see ``.env`` / ``.env.example``). Use this when
    the agent or other callers need the same Arth identity the API uses for data access.
    """
    return _AUTH_USERNAME


def agent_internal_token() -> str:
    """Secret for trusted in-process agent calls (ASGI client header).

    Set ``AGENT_INTERNAL_TOKEN`` in ``.env`` for explicit control. Otherwise a
    deterministic value is derived from ``AUTH_SECRET_KEY`` so the API and agent
    agree without extra configuration (same machine, same process).
    """
    env = os.getenv("AGENT_INTERNAL_TOKEN", "").strip()
    if env:
        return env
    return hashlib.sha256(
        f"{_AUTH_SECRET_KEY}:arth-agent-internal-v1".encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def verify_credentials(username: str, password: str) -> bool:
    """Return True if the username + password match stored credentials.

    Prefer :class:`api.models.AppUser` rows when present (desktop / multi-user);
    otherwise fall back to ``AUTH_USERNAME`` / ``AUTH_PASSWORD`` from ``.env``.
    """
    try:
        from sqlmodel import select

        from api.database import SQLiteSerializingSession, get_engine
        from api.models import AppUser

        with SQLiteSerializingSession(get_engine()) as session:
            user = session.exec(
                select(AppUser).where(AppUser.username == username)
            ).first()
            if user is not None:
                return bcrypt.checkpw(
                    password.encode("utf-8"),
                    user.password_hash.encode("utf-8"),
                )
    except Exception:
        logger.debug("DB credential check failed", exc_info=True)

    username_ok = username == _AUTH_USERNAME
    password_ok = bcrypt.checkpw(password.encode("utf-8"), _PASSWORD_HASH)
    return username_ok and password_ok


def create_session_token(username: str) -> str:
    """Create a signed, time-limited session token containing the username."""
    return _serializer.dumps(username)


def verify_session_token(token: str) -> str:
    """Verify the token signature and expiry. Returns the username on success.

    Raises:
        HTTPException(401) if the token is invalid or expired.
    """
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


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def get_current_user(
    arth_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    x_arth_internal: str | None = Header(default=None, alias="X-Arth-Internal"),
) -> str:
    """FastAPI dependency that enforces authentication.

    Usage:
        @router.get("/protected")
        def protected_endpoint(user: str = Depends(get_current_user)):
            ...

    Or on the whole router:
        app.include_router(router, dependencies=[Depends(get_current_user)])

    Returns the authenticated username. Raises HTTP 401 if not authenticated.

    **Internal agent:** when ``X-Arth-Internal`` matches ``agent_internal_token()``,
    the configured ``AUTH_USERNAME`` is returned (trusted in-process caller only).
    """
    if x_arth_internal and x_arth_internal == agent_internal_token():
        return _AUTH_USERNAME
    if arth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please sign in to continue.",
        )
    return verify_session_token(arth_session)


def effective_user_id(
    user_id: str | None = Query(
        None,
        description="Optional; when set must match the logged-in user (same string as username).",
    ),
    current_user: str = Depends(get_current_user),
) -> str:
    """Resolve the Arth user id for data-scoped endpoints (session wins; param is checked)."""
    out = (user_id or "").strip() or current_user
    if out != current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That user id doesn't match who's signed in.",
        )
    return out
