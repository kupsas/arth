"""
ICICI Direct **equity** rows from the **Trades executed at NSE** email only (PDF → DB).

The exchange email (e.g. ``nse-direct@nse.co.in``) sends a password-protected PDF listing
NSE symbol, buy/sell, quantity, and rate. Password: ``NSE_TRADES_EXECUTED_PASSWORD``.

Parsing: :mod:`pipeline.holding_parsers.icici_direct_contract_note`. No bank ``transform``/LLM —
only :func:`pipeline.holding_pipeline.ingest_investment_transactions` with ``source_type=email``.

ICICI *Order and Trade* / contract-note PDFs are **not** ingested here (by design).
"""

from __future__ import annotations

import datetime
import logging

import pikepdf
import pipeline.config  # noqa: F401 — load ``.env`` before ``os.getenv``

from pipeline.holding_parsers.icici_direct_contract_note import parse_icici_direct_trade_pdf
from pipeline.models import ParsedTransaction
from scraper.email_parsers.base_broker_statement import BaseBrokerStatementParser
from scraper.pdf_passwords import (
    StatementPasswordRequired,
    resolve_nse_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

logger = logging.getLogger(__name__)

# Subject must contain this phrase (case-insensitive), same as live NSE mailers use.
_NSE_TRADES_SUBJECT_PHRASE = "trades executed at nse"


def classify_icici_direct_subject(subject: str) -> str | None:
    """Return a token if *subject* is the NSE trades email; else ``None``."""
    s = (subject or "").lower()
    if _NSE_TRADES_SUBJECT_PHRASE in s:
        return "nse_trades_executed"
    return None


def _nse_trades_pdf_password_candidates() -> list[str]:
    return resolve_nse_pdf_password_candidates()


class ICICIDirectTradeEmailParser(BaseBrokerStatementParser):
    """Decrypt the NSE trades PDF and emit investment rows (no bank ledger rows)."""

    def can_parse(self, sender: str, subject: str) -> bool:
        return classify_icici_direct_subject(subject) is not None

    def parse_attachment(
        self,
        pdf_bytes: bytes,
        received_date: datetime.date,
        *,
        email_sender: str = "",
        email_subject: str = "",
    ) -> list[ParsedTransaction]:
        """Return ``[]`` — investment rows go via :meth:`attachment_investment_outputs`."""
        if classify_icici_direct_subject(email_subject or "") is None:
            logger.warning(
                "ICICIDirectTradeEmailParser.parse_attachment called without "
                "a *Trades executed at NSE* subject"
            )
            return []

        candidates = _nse_trades_pdf_password_candidates()
        if not candidates:
            raise StatementPasswordRequired(
                "nse_trades_executed",
                "Set NSE_TRADES_EXECUTED_PASSWORD or PAN ingredient for derived passwords.",
            )

        try:
            decrypted, _used = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
        except pikepdf.PasswordError as e:
            raise StatementPasswordRequired(
                "nse_trades_executed",
                "None of the NSE PDF password candidates worked. Check env keys and PAN.",
            ) from e
        try:
            self._attachment_inv_txns.extend(
                parse_icici_direct_trade_pdf(
                    decrypted,
                    fallback_trade_date=received_date,
                    aggregate=True,
                )
            )
            if not self._attachment_inv_txns:
                logger.info(
                    "NSE trades PDF produced 0 rows (subject_len=%d) — layout may need parser tweaks",
                    len(email_subject or ""),
                )
        except Exception:
            logger.exception("NSE trades PDF parse failed")
            raise
        finally:
            decrypted.unlink(missing_ok=True)

        return []
