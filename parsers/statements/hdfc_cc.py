"""
HDFC Bank **credit card statement PDF** emails (Swiggy 1905, Diners 5778, etc.).

From addresses vary by product era (``.net`` vs ``.bank.in``) — both are registered in
``scraper/config.py``.  We decrypt with ``HDFC_CC_STATEMENT_PASSWORD``, parse with
:class:`~parsers.uploads.hdfc_cc_pdf.HDFCCreditCardPdfParser`, then stamp the correct
``account_id`` / ``source_key`` for the card inferred from the subject (and PDF text
as a fallback).

Subject routing (order matters — most specific first in :meth:`can_parse`):

1. **Diners Privilege** (current product name) — subject contains ``Diners Privilege``.
2. **Diners Club International** (legacy) — subject contains ``Diners Club``.
3. **Swiggy / 1905** — subject contains ``Swiggy``.
4. Otherwise, if the subject still looks like an HDFC CC statement, we inspect the
   decrypted PDF for ``…XXXX…1905`` vs ``…5778`` and map to the right account.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import ClassVar

import pikepdf
import pipeline.config  # noqa: F401 — load ``.env`` before ``os.getenv``

from pipeline.models import ParsedTransaction
from parsers.uploads.hdfc_cc_pdf import HDFCCreditCardPdfParser
from parsers.statements.base import BaseStatementEmailParser
from scraper.pdf_passwords import (
    StatementPasswordRequired,
    resolve_hdfc_cc_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

logger = logging.getLogger(__name__)

def _pdf_card_tail(pdf_path) -> str | None:
    """Read page 1 text and return the last 4 digits of the printed card number.

    Example line: ``Credit Card No. 526873XXXXXX1905`` → ``1905`` (not the postal code
    elsewhere on the page).
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return None
        text = pdf.pages[0].extract_text() or ""

    # Do not allow ``\\s`` in the capture — PDF text can break right after ``1905`` and
    # pull ``5042`` from the address line into the same match.
    m = re.search(
        r"Credit\s+Card\s+No\.\s*([0-9X]+)",
        text,
        re.IGNORECASE,
    )
    if m:
        digits = "".join(c for c in m.group(1) if c.isdigit())
        if len(digits) >= 4:
            return digits[-4:]

    # Fallback: masked PAN on one line
    for line in text.splitlines():
        if "XXXX" in line and "Credit" not in line:
            compact = line.replace(" ", "")
            m2 = re.search(r"(\d{4})\s*$", compact)
            if m2 and "XXXX" in compact:
                return m2.group(1)
    return None


class HDFCCCStatementEmailParser(BaseStatementEmailParser):
    """Decrypt HDFC CC PDF attachments and parse with :class:`HDFCCreditCardPdfParser`."""

    parse_type: ClassVar[str] = "statement"

    def can_parse(self, sender: str, subject: str) -> bool:
        # Sender is scoped in ``EMAIL_PARSER_REGISTRY`` to ``emailstatements.cards@…``
        # only — a broad subject match is enough (Swiggy, Diners Club, etc.).
        return "credit card statement" in subject.lower()

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        candidates = resolve_hdfc_cc_pdf_password_candidates()
        if not candidates:
            raise StatementPasswordRequired(
                "hdfc_cc_statement",
                "HDFC credit card PDF needs HDFC_CC_STATEMENT_PASSWORD, or name + date of "
                "birth in onboarding (see password ingredients).",
            )

        last4 = None
        try:
            decrypted, _used = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
        except pikepdf.PasswordError as e:
            raise StatementPasswordRequired(
                "hdfc_cc_statement",
                "None of the HDFC credit card PDF password candidates worked. Check env "
                "keys, registered name, and date of birth.",
            ) from e
        try:
            last4 = _pdf_card_tail(decrypted)
            if last4 is None:
                logger.warning(
                    "Could not map HDFC CC PDF to an account "
                    "(configured_card_slots=%d subject_len=%d inferred_tail=no)",
                    len(self.accounts),
                    len(email_subject or ""),
                )
                return []

            entry = self.accounts.get(last4)
            if not entry:
                account_id = f"HDFC_CC_{last4}"
                source_key = f"hdfc_cc_{last4}"
                logger.info(
                    "HDFC CC PDF tail …%s not in configured slots (%d); using %s",
                    last4,
                    len(self.accounts),
                    source_key,
                )
            else:
                account_id = entry["account_id"]
                source_key = entry["source_key"]

            rows = HDFCCreditCardPdfParser().parse(decrypted)
            return [_stamp(r, account_id, source_key) for r in rows]
        finally:
            decrypted.unlink(missing_ok=True)


def _stamp(pt: ParsedTransaction, account_id: str, source_key: str) -> ParsedTransaction:
    """Attach orchestrator metadata — same pattern as ICICI statement emails."""
    return pt.model_copy(
        update={
            "metadata": {
                **pt.metadata,
                "account_id": account_id,
                "source_key": source_key,
                "email_source": "hdfc_cc_statement_pdf",
            }
        }
    )
