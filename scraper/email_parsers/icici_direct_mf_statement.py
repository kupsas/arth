"""
ICICI Securities **Mutual Fund Account Statement** email (password-protected PDF).

Same password family as ICICI Bank statements — :func:`~scraper.pdf_passwords.resolve_icici_statement_pdf_password_candidates`.

Emits ``ParsedInvestmentTxn`` rows plus ``ParsedHolding`` rows derived via
:func:`pipeline.holding_parsers.icici_direct_mf.derive_mf_holdings` for the
transactions observed on this statement (snapshot consistent with CSV ingest).
"""

from __future__ import annotations

import datetime
import logging

import pikepdf
import pipeline.config  # noqa: F401

from pipeline.holding_parsers.icici_direct_mf import derive_mf_holdings
from pipeline.holding_parsers.icici_direct_mf_statement_pdf import (
    parse_icici_direct_mf_statement_pdf,
)
from pipeline.models import ParsedTransaction
from scraper.email_parsers.base_broker_statement import BaseBrokerStatementParser
from scraper.pdf_passwords import (
    StatementPasswordRequired,
    resolve_icici_statement_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

logger = logging.getLogger(__name__)


def classify_icici_mf_statement_subject(subject: str) -> bool:
    s = (subject or "").lower()
    return "mutual fund account statement" in s


class ICICIDirectMFStatementEmailParser(BaseBrokerStatementParser):
    """Decrypt MF account statement PDF → MF txns + derived holdings snapshot."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return classify_icici_mf_statement_subject(subject)

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        if not classify_icici_mf_statement_subject(email_subject or ""):
            logger.warning(
                "ICICIDirectMFStatementEmailParser called without MF account statement subject"
            )
            return []

        candidates = resolve_icici_statement_pdf_password_candidates()
        if not candidates:
            raise StatementPasswordRequired(
                "icici_direct_statement",
                "Set ICICI PDF env keys or save registered name + DOB for MF statements.",
            )

        try:
            decrypted, _used = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
        except pikepdf.PasswordError as e:
            raise StatementPasswordRequired(
                "icici_direct_statement",
                "None of the ICICI Securities MF PDF password candidates worked.",
            ) from e

        try:
            txns = parse_icici_direct_mf_statement_pdf(decrypted)
            self._attachment_inv_txns.extend(txns)
            if txns:
                self._attachment_holdings.extend(derive_mf_holdings(txns))
            else:
                logger.info(
                    "ICICI MF statement PDF produced 0 rows (subject_len=%d)",
                    len(email_subject or ""),
                )
        except Exception:
            logger.exception("ICICI MF account statement PDF parse failed")
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        return []
