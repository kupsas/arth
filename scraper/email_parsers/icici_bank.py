"""
ICICI Bank email alert parser.

Handles transaction alert emails from customernotification@icici.bank.in.

ICICI sends transaction alerts for outbound net banking transfers (IMPS and NEFT)
triggered via iMobile. Both types share the exact same email template and format —
only the payment method name ("IMPS" vs "NEFT") and transaction ID format differ.

Subject patterns:
  - "IMPS transaction through ICICI Bank iMobile."
  - "NEFT transaction through ICICI Bank iMobile."

Body text (inside a <font> tag in the email):
    You have made an online IMPS payment of Rs. 1.00 towards SASHANK SAI KUPPA
    on Mar 19, 2026 at 12:07 a.m. from your ICICI Bank Savings Account XXXX6118.
    The Transaction ID is 607800230914.

Date format: "Mar 19, 2026" (%b %d, %Y) — note day before month for ICICI.
Amount format: "Rs. X.XX" with a space after "Rs." (unlike HDFC's "Rs.X").

What ICICI emails do NOT contain (and why the parser's scope is limited):
  - Inbound transfers (salary, NEFT received) — no email alerts sent by ICICI
  - ICICI Direct / broker transactions — no transactional email at all
  - MAB reminders from customercare@icicibank.com — not transactions, not parsed
"""

from __future__ import annotations

import datetime
import logging
import re
from decimal import Decimal

from bs4 import BeautifulSoup

from pipeline.models import ParsedTransaction
from scraper.email_parsers.base import BaseEmailParser

logger = logging.getLogger(__name__)


def _extract_icici_body_text(html: str) -> str:
    """Extract plain text from ICICI's email template.

    ICICI's template is simpler than HDFC's — the transaction sentence lives
    inside a <font> tag inside a plain <td>. Rather than hunting for that
    specific element, we get_text() the whole body: ICICI's regex patterns
    are specific enough to cut through the surrounding boilerplate.
    """
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def _parse_mon_dd_yyyy(s: str) -> datetime.date | None:
    """Parse 'Mon DD, YYYY' format used by ICICI alerts, e.g. 'Mar 19, 2026'."""
    try:
        return datetime.datetime.strptime(s.strip(), "%b %d, %Y").date()
    except ValueError:
        return None


class ICICINetBankingParser(BaseEmailParser):
    """Parses ICICI IMPS and NEFT outbound transaction alert emails.

    Both IMPS and NEFT share an identical email template — the only differences
    are the payment method string ("IMPS"/"NEFT") and the transaction ID format:
      - IMPS ID: purely numeric, e.g. "607800230914"
      - NEFT ID: alphanumeric with prefix, e.g. "IN12607828774378"

    One regex handles both.
    """

    # Regex groups: (txn_method, amount, beneficiary, date_str, acct_last4, txn_id)
    #
    # Key regex decisions:
    #   - `Rs\.\s*` handles the space in "Rs. 1.00" (ICICI style, unlike HDFC)
    #   - `[X]+(\d{4})` handles ICICI's "XXXX6118" masking (uppercase X)
    #   - `[ap]\.m\.` matches both "a.m." and "p.m." with escaped dots
    #   - `.+?` for beneficiary is non-greedy, stops at " on <date>"
    _PATTERN = re.compile(
        r"You have made an online\s+(IMPS|NEFT)\s+payment of Rs\.\s*"
        r"([\d,]+(?:\.\d+)?)"              # amount
        r"\s+towards\s+(.+?)"             # beneficiary name (non-greedy)
        r"\s+on\s+(\w+\s+\d{1,2},\s+\d{4})"   # "Mar 19, 2026"
        r"\s+at\s+.+?"                    # "at 12:07 a.m." — consumed but not captured
        r"from your ICICI Bank Savings Account\s+[X]+(\d{4})"  # "XXXX6118"
        r".+?The Transaction ID is\s+(\S+?)\.?\s",   # transaction ID (strip trailing period)
        re.IGNORECASE | re.DOTALL,
    )

    def can_parse(self, sender: str, subject: str) -> bool:
        # Both IMPS and NEFT subjects contain this phrase
        return "transaction through icici bank imobile" in subject.lower()

    def parse(self, html_body: str, received_date: datetime.date) -> list[ParsedTransaction]:
        text = _extract_icici_body_text(html_body)
        m = self._PATTERN.search(text)
        if not m:
            # Never log raw email body — it contains amounts, counterparty names, and masked account tails.
            logger.warning(
                "ICICINetBankingParser: regex did not match (extracted_plain_text_len=%d)",
                len(text),
            )
            return []

        txn_method, amount_str, beneficiary, date_str, acct_last4, txn_id = m.groups()
        amount = Decimal(amount_str.replace(",", ""))

        txn_date = _parse_mon_dd_yyyy(date_str)
        if txn_date is None:
            logger.warning(
                "ICICINetBankingParser: unrecognised date format '%s', "
                "falling back to email received date %s",
                date_str,
                received_date,
            )
            txn_date = received_date

        account_info = self._lookup_account(acct_last4)
        if account_info is None:
            return []
        account_id, source_key = account_info

        # Collapse any HTML whitespace artifacts in beneficiary name
        beneficiary = " ".join(beneficiary.split())
        txn_method = txn_method.upper()

        return [
            ParsedTransaction(
                txn_date=txn_date,
                # "IMPS: SASHANK SAI KUPPA" or "NEFT: ..."
                raw_description=f"{txn_method}: {beneficiary}",
                debit_amount=amount,
                credit_amount=Decimal("0"),
                ref_number=txn_id,
                metadata={
                    "account_id": account_id,
                    "source_key": source_key,
                    "channel_hint": "BANK",
                    "txn_method": txn_method,
                    "email_source": "icici_netbanking_alert",
                },
            )
        ]
