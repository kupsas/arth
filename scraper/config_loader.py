"""
Load bank sender → parser / account mappings from SQLite (per user), with
fallback to :data:`scraper.config.BANK_SENDERS` when the DB has no rows.

See DESKTOP_PREREQS item 1 — config must not be hardcoded for multi-user installs.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from sqlmodel import Session, select

from scraper.config import BANK_SENDERS
from scraper.email_router import _normalise_sender

logger = logging.getLogger(__name__)

# Type alias: same nested shape as ``BANK_SENDERS`` in scraper/config.py.
BankSendersConfig = dict[str, dict[str, Any]]


def get_bank_senders_config(session: Session, user_id: str) -> BankSendersConfig:
    """Return sender → {accounts, first_run_lookback_days?, parser_key?} for ``user_id``.

    When the DB has no ``scraper_bank_senders`` rows for anyone, returns a deep
    copy of the static ``BANK_SENDERS`` dict from ``scraper.config`` (legacy
    single-user behaviour) and logs once at INFO.
    """
    from api.models import ScraperAccountMapping, ScraperBankSender

    uid = (user_id or "").strip() or "sashank"

    senders = session.exec(
        select(ScraperBankSender).where(ScraperBankSender.user_id == uid)
    ).all()
    if not senders:
        logger.info(
            "No scraper_bank_senders for user_id=%r — using scraper.config.BANK_SENDERS",
            uid,
        )
        return copy.deepcopy(BANK_SENDERS)

    out: BankSendersConfig = {}
    for s in senders:
        if not s.enabled:
            continue
        key = _normalise_sender(s.sender_email)
        mappings = session.exec(
            select(ScraperAccountMapping).where(
                ScraperAccountMapping.user_id == uid,
                ScraperAccountMapping.sender_email == key,
            )
        ).all()
        accounts: dict[str, dict[str, str]] = {}
        for m in mappings:
            accounts[m.last_4_digits] = {
                "account_id": m.account_id,
                "source_key": m.source_key,
            }
        entry: dict[str, Any] = {"accounts": accounts}
        if s.parser_key:
            entry["parser_key"] = s.parser_key
        if s.first_run_lookback_days is not None:
            entry["first_run_lookback_days"] = s.first_run_lookback_days
        out[key] = entry
    if not out:
        logger.warning(
            "DB scraper config for %r produced empty dict — falling back to file",
            uid,
        )
        return copy.deepcopy(BANK_SENDERS)
    return out


def all_sender_emails(bank: BankSendersConfig) -> list[str]:
    """Sorted list of raw sender keys for iteration (deterministic order)."""
    return sorted(bank.keys())
