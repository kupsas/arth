"""
Map bank ``account_id`` (from ``Transaction``) to Arth ``user_id``.

Transactions do not store ``user_id`` directly — the link is via which bank account
the row came from. For a 2-person household, configure mapping in production
with env ``ARTH_ACCOUNT_USER_MAP`` (JSON object: account_id -> user_id).

Example::

    export ARTH_ACCOUNT_USER_MAP='{"HDFC_SAL_3703":"sashank","ICICI_SAV_9999":"aditi"}'

When unmapped, accounts default to ``sashank`` (single-user / legacy behaviour).

Tests can call :func:`register_account_for_user` to bind accounts without env vars.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from sqlmodel import Session

logger = logging.getLogger(__name__)

_DEFAULT_USER: Final[str] = "sashank"

# Test-only overrides (cleared between tests if needed).
_test_overrides: dict[str, str] = {}


def register_account_for_user(account_id: str, user_id: str) -> None:
    """Bind an account to a user (intended for tests)."""
    _test_overrides[account_id] = user_id


def clear_test_overrides() -> None:
    """Remove all test bindings."""
    _test_overrides.clear()


def _env_mapping() -> dict[str, str]:
    raw = os.getenv("ARTH_ACCOUNT_USER_MAP", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        logger.warning("ARTH_ACCOUNT_USER_MAP is not valid JSON; ignoring")
        return {}


def user_id_for_account(account_id: str, session: Session | None = None) -> str:
    """Return the Arth user that owns this bank account.

    When ``session`` is set, :class:`api.models.ScraperAccountMapping` rows are
    consulted first (DESKTOP_PREREQS — DB-backed account ownership).
    """
    if account_id in _test_overrides:
        return _test_overrides[account_id]
    if session is not None:
        from sqlmodel import select

        from api.models import ScraperAccountMapping

        m = session.exec(
            select(ScraperAccountMapping).where(
                ScraperAccountMapping.account_id == account_id
            )
        ).first()
        if m is not None:
            return str(m.user_id)
    env_m = _env_mapping()
    if account_id in env_m:
        return env_m[account_id]
    return _DEFAULT_USER


def transaction_belongs_to_user(account_id: str, user_id: str) -> bool:
    """True if this account is mapped to ``user_id``."""
    return user_id_for_account(account_id) == user_id
