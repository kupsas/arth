"""
HDFC **Combined Email Statement** PDF (monthly savings + summary).

These arrive from ``HDFC Bank Smart Statement <hdfcbanksmartstatement@hdfcbank.…>``
(same sender historically used for pre-combined "Email Account Statement" — we only
parse subjects that clearly identify the **combined** product).

Steps:
  1. Match subject (see :meth:`HDFCCombinedStatementEmailParser.can_parse`).
  2. Decrypt attachment with passwords from :mod:`scraper.pdf_passwords` (see ``.env``).
  3. Parse with :class:`~pipeline.parsers.hdfc_savings_pdf.HDFCSavingsPdfParser`.
  4. Stamp ``account_id`` / ``source_key`` for savings **3703** from the scraper config
     (same tail-key pattern as InstaAlerts).
"""

from __future__ import annotations

import datetime
import logging
from typing import ClassVar

import pikepdf
import pipeline.config  # noqa: F401 — load ``.env`` before ``os.getenv``

from pipeline.models import ParsedTransaction
from pipeline.parsers.hdfc_savings_pdf import HDFCSavingsPdfParser
from scraper.email_parsers.base_statement import BaseStatementEmailParser
from scraper.pdf_passwords import (
    StatementPasswordRequired,
    resolve_hdfc_combined_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

logger = logging.getLogger(__name__)


class HDFCCombinedStatementEmailParser(BaseStatementEmailParser):
    """Decrypt and parse HDFC combined monthly statement PDFs."""

    parse_type: ClassVar[str] = "attachment"

    def can_parse(self, sender: str, subject: str) -> bool:
        sl = subject.lower()
        # Current product (from ~Feb 2024): "HDFC Bank Combined Email Statement for …"
        if "combined email statement" in sl:
            return True
        return False

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        candidates = resolve_hdfc_combined_pdf_password_candidates()
        if not candidates:
            raise StatementPasswordRequired(
                "hdfc_combined_statement",
                "HDFC combined statement needs HDFC_STATEMENT_PASSWORD or HDFC customer ID "
                "in onboarding.",
            )

        # Single savings account in config: last four digits of the account number (3703).
        if "3703" not in self.accounts:
            logger.error(
                "HDFC combined statement parser has no account mapping for 3703 — check BANK_SENDERS."
            )
            return []

        entry = self.accounts["3703"]
        account_id = entry["account_id"]
        source_key = entry["source_key"]

        try:
            decrypted, _used = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
        except pikepdf.PasswordError as e:
            raise StatementPasswordRequired(
                "hdfc_combined_statement",
                "None of the HDFC combined PDF password candidates worked. Check env keys "
                "and customer ID.",
            ) from e
        try:
            rows = HDFCSavingsPdfParser().parse(decrypted)
            return [_stamp(r, account_id, source_key) for r in rows]
        finally:
            decrypted.unlink(missing_ok=True)


def _stamp(pt: ParsedTransaction, account_id: str, source_key: str) -> ParsedTransaction:
    return pt.model_copy(
        update={
            "metadata": {
                **pt.metadata,
                "account_id": account_id,
                "source_key": source_key,
                "email_source": "hdfc_combined_statement_pdf",
            }
        }
    )
