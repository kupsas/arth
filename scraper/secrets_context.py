"""
Thread-local context for PDF / statement secrets during email processing.

Parsers call :func:`resolve_secret_env` instead of raw ``os.getenv`` so the setup
wizard can store passwords in :class:`api.models.UserSecrets` (encrypted).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator

from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_ctx_session: ContextVar[Session | None] = ContextVar("arth_pdf_session", default=None)
_ctx_user_id: ContextVar[str | None] = ContextVar("arth_pdf_user_id", default=None)


@contextmanager
def statement_secrets_context(
    session: Session | None,
    user_id: str | None,
) -> Generator[None, None, None]:
    """Set DB + user scope for :func:`resolve_secret_env` for one email parse."""
    tok_s = _ctx_session.set(session)
    tok_u = _ctx_user_id.set(user_id)
    try:
        yield
    finally:
        _ctx_session.reset(tok_s)
        _ctx_user_id.reset(tok_u)


def get_statement_secrets_scope() -> tuple[Session | None, str | None]:
    """Return (session, user_id) when inside :func:`statement_secrets_context`, else (None, None)."""
    return _ctx_session.get(), _ctx_user_id.get()


def resolve_secret_env(env_key: str, default: str = "") -> str:
    """Return secret for ``env_key``: UserSecrets JSON (if in context), else ``os.getenv``."""
    session = _ctx_session.get()
    uid = (_ctx_user_id.get() or "").strip()
    if session is not None and uid:
        try:
            from api.models import UserSecrets

            row = session.exec(select(UserSecrets).where(UserSecrets.user_id == uid)).first()
            if row and row.secrets_json:
                data = json.loads(row.secrets_json)
                if isinstance(data, dict) and env_key in data and data[env_key]:
                    return str(data[env_key])
        except Exception:
            logger.debug("UserSecrets lookup failed for %s", env_key, exc_info=True)
    return os.getenv(env_key, default)
