"""
Abstract base class for all bank email alert parsers.

Every concrete parser must implement two methods:
  - can_parse(sender, subject) → bool   : routing check, no HTML required
  - parse(html_body, received_date) → list[ParsedTransaction]

Design contract for metadata dict on each returned ParsedTransaction:
  REQUIRED:
    metadata["account_id"]  : str  — e.g. "HDFC_SAL_3703"
    metadata["source_key"]  : str  — e.g. "hdfc_savings"
  OPTIONAL (used by rules_classifier):
    metadata["channel_hint"]: str  — "UPI" | "CARD" | "BANK"
    metadata["vpa"]         : str  — UPI virtual payment address
    metadata["txn_method"]  : str  — "IMPS" | "NEFT" (for bank transfers)
    metadata["email_source"]: str  — which parser produced this (for debugging)

The orchestrator reads account_id and source_key from metadata to call
transform() with the correct account context.
"""

from __future__ import annotations

import datetime
import logging
from abc import ABC, abstractmethod

from pipeline.models import ParsedTransaction

logger = logging.getLogger(__name__)


class BaseEmailParser(ABC):
    """Abstract base for all bank email alert parsers."""

    def __init__(self, accounts: dict[str, dict]) -> None:
        """
        Args:
            accounts: The per-sender accounts mapping from BANK_SENDERS config.
                      Keys are last-4-digits strings; values are dicts with
                      "account_id" and "source_key".
                      Example:
                        {
                          "3703": {"account_id": "HDFC_SAL_3703", "source_key": "hdfc_savings"},
                          "1905": {"account_id": "HDFC_CC_1905", "source_key": "hdfc_cc_1905"},
                        }
        """
        self.accounts = accounts

    @abstractmethod
    def can_parse(self, sender: str, subject: str) -> bool:
        """Return True if this parser handles this sender + subject combination.

        This is called before downloading the email body, so it must be fast
        (string matching only — no regex, no HTML parsing).
        """
        ...

    @abstractmethod
    def parse(
        self, html_body: str, received_date: datetime.date
    ) -> list[ParsedTransaction]:
        """Parse the email body and return zero or more ParsedTransactions.

        Returns an empty list for non-transaction emails (e.g. card settings
        change, MAB reminder, etc.) — this is valid and expected behavior.

        Args:
            html_body:     The decoded HTML content of the email.
            received_date: The date Gmail received this email (UTC).
                           Used as a fallback date if parsing fails.
        """
        ...

    def _lookup_account(self, last4: str) -> tuple[str, str] | None:
        """Map last-4-digits to (account_id, source_key) using the accounts config.

        Returns None if the account isn't configured (logs a warning).
        This can happen if you receive an alert for a card/account that wasn't
        added to BANK_SENDERS in config.py.
        """
        entry = self.accounts.get(last4)
        if not entry:
            logger.warning(
                "%s: received alert for an account/card tail not present in BANK_SENDERS "
                "— skipping transaction. Add the mapping in scraper/config.py if you want it tracked.",
                type(self).__name__,
            )
            return None
        return entry["account_id"], entry["source_key"]
