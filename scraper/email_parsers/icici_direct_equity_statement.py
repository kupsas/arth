"""
ICICI Securities **Equity Transaction Statement** email (password-protected PDF).

Sender: ``service@icicisecurities.com``. Password chain:
:data:`~scraper.pdf_passwords.ICICI_DIRECT_STATEMENT_PASSWORD_KEYS`.

Produces ``ParsedInvestmentTxn`` rows and, when the PDF has activity, a derived
``ParsedHolding`` snapshot per NSE symbol (same path as the MF statement parser).
"""

from __future__ import annotations

import datetime
import logging

import pipeline.config  # noqa: F401 — load ``.env``

from pipeline.holding_parsers.derived_equity import derive_equity_holdings
from pipeline.holding_parsers.icici_direct_equity_statement_pdf import (
    parse_icici_direct_equity_statement_pdf,
)
from pipeline.models import ParsedTransaction
from scraper.email_parsers.base_broker_statement import BaseBrokerStatementParser
from scraper.pdf_passwords import ICICI_DIRECT_STATEMENT_PASSWORD_KEYS, resolve_pdf_password_chain
from scraper.pdf_utils import decrypt_pdf

logger = logging.getLogger(__name__)


def _statement_pdf_password() -> tuple[str, str]:
    p = resolve_pdf_password_chain(*ICICI_DIRECT_STATEMENT_PASSWORD_KEYS)
    return (p, ICICI_DIRECT_STATEMENT_PASSWORD_KEYS[0])


def classify_icici_equity_statement_subject(subject: str) -> bool:
    """True if subject is an equity transaction statement from ICICI Securities."""
    s = (subject or "").lower()
    return "equity transaction statement" in s


class ICICIDirectEquityStatementEmailParser(BaseBrokerStatementParser):
    """Decrypt equity transaction statement PDFs → investment legs + FIFO-derived holdings."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return classify_icici_equity_statement_subject(subject)

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        if not classify_icici_equity_statement_subject(email_subject or ""):
            logger.warning(
                "ICICIDirectEquityStatementEmailParser called without equity statement subject"
            )
            return []

        password, env_key = _statement_pdf_password()
        if not password:
            logger.error(
                "Missing %s — cannot decrypt ICICI equity transaction statement PDF.",
                env_key,
            )
            return []

        decrypted = decrypt_pdf(pdf_bytes, password)
        try:
            rows = parse_icici_direct_equity_statement_pdf(decrypted, aggregate=True)
            self._attachment_inv_txns.extend(rows)
            if rows:
                # Same idea as ``ICICIDirectMFStatementEmailParser``: seed ``Holding`` rows
                # at email-parse time so the portfolio is visible before a separate derive pass.
                self._attachment_holdings.extend(derive_equity_holdings(rows))
            if not rows:
                logger.info(
                    "ICICI equity statement PDF produced 0 rows (subject=%r)",
                    (email_subject or "")[:120],
                )
        except Exception:
            logger.exception("ICICI equity transaction statement PDF parse failed")
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        return []
