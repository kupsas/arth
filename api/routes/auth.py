"""
Authentication routes — login, logout, session status.

POST /api/auth/login   → set session cookie (local install; no password check)
POST /api/auth/logout  → clear session cookie
GET  /api/auth/me      → return current user info (requires valid session)

Cookie attributes:
  - httponly=True   — JavaScript cannot read it (XSS protection)
  - samesite="lax"  — sent on top-level navigations but not cross-site POSTs
                      (CSRF protection for same-origin use)
  - path="/"        — valid for all paths
  - secure=False    — localhost only; set to True if serving over HTTPS
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from api.auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    get_current_user,
)
from api.constants import DEFAULT_LOCAL_USER

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class AuthStatusResponse(BaseModel):
    authenticated: bool
    username: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/login", response_model=AuthStatusResponse)
def login(body: LoginRequest, response: Response) -> AuthStatusResponse:
    """Issue a session cookie for the local install (password is not validated)."""
    _ = body
    token = create_session_token(DEFAULT_LOCAL_USER)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    logger.info("Session cookie issued for local user")
    return AuthStatusResponse(authenticated=True, username=DEFAULT_LOCAL_USER)


@router.post("/logout", response_model=AuthStatusResponse)
def logout(response: Response) -> AuthStatusResponse:
    """Clear the session cookie, effectively logging the user out."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return AuthStatusResponse(authenticated=False)


@router.get("/me", response_model=AuthStatusResponse)
def me(username: str = Depends(get_current_user)) -> AuthStatusResponse:
    """Return the current session's username.

    The dashboard calls this on load to check if the session is still valid
    without decoding the cookie client-side (it's httpOnly — JS can't read it).
    Returns 401 if the session is missing or expired.
    """
    return AuthStatusResponse(authenticated=True, username=username)
