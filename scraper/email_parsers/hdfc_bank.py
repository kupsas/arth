"""
HDFC Bank email alert parsers.

All three parsers handle emails from alerts@hdfcbank.net, routed by subject:

┌─────────────────────────────────────────┬────────────────────────────────────────┐
│ Subject trigger                         │ Parser                                 │
├─────────────────────────────────────────┼────────────────────────────────────────┤
│ "debited via Credit Card"               │ HDFCCreditCardAlertParser              │
│ "UPI txn"                               │ HDFCUPIAlertParser (outbound)          │
│ "Account update for your HDFC Bank A/c" │ HDFCAccountUpdateParser (inbound UPI   │
│                                         │  + skips E-mandate / card settings)    │
└─────────────────────────────────────────┴────────────────────────────────────────┘

All parsers use the same HTML extraction strategy: find the <td class="esd-text">
element that HDFC always puts transaction text in, then run regex against the
plain text. This makes parsers resilient to HDFC's table layout changing.

Date formats encountered in real emails:
  - "14 Mar, 2026"   → CC alerts        (%d %b, %Y)
  - "15-03-26"       → UPI alerts       (%d-%m-%y)   ← 2-digit year!
  - "02-02-26"       → Account updates  (%d-%m-%y)

The 2-digit year means dates before 2000 can't exist — fine for a finance app.
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


# ─── Shared HTML extraction ─────────────────────────────────────────────────────

def _extract_hdfc_body_text(html: str) -> str:
    """Pull plain text from HDFC's standard email template.

    HDFC emails always embed transaction text inside <td class="td esd-text">
    elements (there are several — most are empty spacers, one has the real text).
    We grab all of them, join with spaces, and let the caller's regex find the data.

    Falls back to the full page text if the expected structure isn't found.
    """
    soup = BeautifulSoup(html, "html.parser")
    tds = soup.find_all("td", class_="esd-text")
    if tds:
        parts = [td.get_text(separator=" ", strip=True) for td in tds]
        combined = " ".join(p for p in parts if p)
        if combined.strip():
            return combined
    # Fallback — less precise but still regex-matchable
    return soup.get_text(separator=" ", strip=True)


# ─── Date parsing helpers ────────────────────────────────────────────────────────

def _parse_ddmmyy(s: str) -> datetime.date | None:
    """Parse 'DD-MM-YY' (2-digit year) used in HDFC UPI alerts, e.g. '15-03-26'."""
    try:
        return datetime.datetime.strptime(s.strip(), "%d-%m-%y").date()
    except ValueError:
        return None


def _parse_dd_mon_yyyy(s: str) -> datetime.date | None:
    """Parse 'DD Mon, YYYY' used in HDFC CC alerts, e.g. '14 Mar, 2026'."""
    try:
        return datetime.datetime.strptime(s.strip(), "%d %b, %Y").date()
    except ValueError:
        return None


# ─── Parser 1: HDFC Credit Card Alert ───────────────────────────────────────────

class HDFCCreditCardAlertParser(BaseEmailParser):
    """Parses HDFC CC swipe alert emails.

    Subject pattern: "debited via Credit Card"
    Example subject: "Rs.1014.00 debited via Credit Card **1905"

    Body text (inside td.esd-text):
        Rs.1014.00 is debited from your HDFC Bank Credit Card ending 1905
        towards PYU*Swiggy Food on 14 Mar, 2026 at 19:55:53.
    """

    # Regex groups: (amount, card_last4, merchant, date_str)
    # We stop capturing merchant at "on <date>" — non-greedy (.+?) handles this.
    _PATTERN = re.compile(
        r"Rs\.(\d[\d,]*(?:\.\d+)?)"
        r"\s+is debited from your HDFC Bank Credit Card ending\s+(\d{4})"
        r"\s+towards\s+(.+?)"
        r"\s+on\s+(\d{1,2}\s+\w+,\s+\d{4})"   # "14 Mar, 2026"
        r"\s+at",                                # stop before the time
        re.IGNORECASE | re.DOTALL,
    )

    def can_parse(self, sender: str, subject: str) -> bool:
        return "debited via credit card" in subject.lower()

    def parse(self, html_body: str, received_date: datetime.date) -> list[ParsedTransaction]:
        text = _extract_hdfc_body_text(html_body)
        m = self._PATTERN.search(text)
        if not m:
            logger.warning(
                "HDFCCreditCardAlertParser: regex did not match — body text was:\n%s",
                text[:300],
            )
            return []

        amount_str, card_last4, merchant, date_str = m.groups()
        amount = Decimal(amount_str.replace(",", ""))

        txn_date = _parse_dd_mon_yyyy(date_str)
        if txn_date is None:
            logger.warning(
                "HDFCCreditCardAlertParser: unrecognised date format '%s', "
                "falling back to email received date %s",
                date_str,
                received_date,
            )
            txn_date = received_date

        account_info = self._lookup_account(card_last4)
        if account_info is None:
            return []
        account_id, source_key = account_info

        # Collapse whitespace in merchant name (HTML whitespace can be noisy)
        merchant = " ".join(merchant.split())

        return [
            ParsedTransaction(
                txn_date=txn_date,
                # raw_description mirrors the style of CC statement descriptions
                raw_description=f"CC: {merchant}",
                debit_amount=amount,
                credit_amount=Decimal("0"),
                metadata={
                    "account_id": account_id,
                    "source_key": source_key,
                    "channel_hint": "CARD",
                    "card_last4": card_last4,
                    "email_source": "hdfc_cc_alert",
                },
            )
        ]


# ─── Parser 2: HDFC UPI Outbound Alert ──────────────────────────────────────────

class HDFCUPIAlertParser(BaseEmailParser):
    """Parses HDFC UPI outbound debit alert emails.

    Subject pattern: "UPI txn" (with the ❗ emoji prefix in real emails)
    Example subject: "❗  You have done a UPI txn. Check details!"

    Body text:
        Rs.951.00 has been debited from account 3703 to VPA eatclub@icici
        EatClub on 15-03-26.
        Your UPI transaction reference number is 120080887305.
    """

    # Regex groups: (amount, acct_last4, vpa, merchant, date_str)
    _PATTERN = re.compile(
        r"Rs\.(\d[\d,]*(?:\.\d+)?)"
        r"\s+has been debited from account\s+(\d{4})"
        r"\s+to VPA\s+(\S+)"        # VPA has no internal spaces
        r"\s+(.+?)"                  # merchant name (non-greedy)
        r"\s+on\s+(\d{2}-\d{2}-\d{2})",   # "15-03-26"
        re.IGNORECASE | re.DOTALL,
    )

    _REF_PATTERN = re.compile(
        r"UPI transaction reference number is\s+(\d+)",
        re.IGNORECASE,
    )

    def can_parse(self, sender: str, subject: str) -> bool:
        return "upi txn" in subject.lower()

    def parse(self, html_body: str, received_date: datetime.date) -> list[ParsedTransaction]:
        text = _extract_hdfc_body_text(html_body)
        m = self._PATTERN.search(text)
        if not m:
            logger.warning(
                "HDFCUPIAlertParser: regex did not match — body text was:\n%s",
                text[:300],
            )
            return []

        amount_str, acct_last4, vpa, merchant, date_str = m.groups()
        amount = Decimal(amount_str.replace(",", ""))

        txn_date = _parse_ddmmyy(date_str)
        if txn_date is None:
            logger.warning(
                "HDFCUPIAlertParser: unrecognised date '%s', falling back to %s",
                date_str,
                received_date,
            )
            txn_date = received_date

        account_info = self._lookup_account(acct_last4)
        if account_info is None:
            return []
        account_id, source_key = account_info

        # Extract ref number if present (it's on the next sentence)
        ref_m = self._REF_PATTERN.search(text)
        ref_number = ref_m.group(1) if ref_m else None

        merchant = " ".join(merchant.split())

        return [
            ParsedTransaction(
                txn_date=txn_date,
                raw_description=f"UPI: {vpa} {merchant}",
                debit_amount=amount,
                credit_amount=Decimal("0"),
                ref_number=ref_number,
                metadata={
                    "account_id": account_id,
                    "source_key": source_key,
                    "channel_hint": "UPI",
                    "vpa": vpa,
                    "email_source": "hdfc_upi_outbound",
                },
            )
        ]


# ─── Parser 3: HDFC Account Update (UPI inbound + skippables) ───────────────────

class HDFCAccountUpdateParser(BaseEmailParser):
    """Parses "Account update for your HDFC Bank A/c" emails.

    HDFC uses this subject for multiple notification types:
      1. UPI inbound credit to savings account  → produces a ParsedTransaction
      2. E-mandate (auto-payment) on CC         → no amount in email, skip
      3. Card settings change / other notices   → not a transaction, skip

    We try the UPI inbound pattern first. If it doesn't match, we return []
    rather than failing — non-transaction "Account update" emails are expected.

    Body text for UPI inbound (shape; test fixtures use synthetic VPA / names):
        Rs. 950.00 is successfully credited to your account **3703 by VPA
        sender.demo@okhdfcbank EXAMPLE RECEIVER on 02-02-26.
        Your UPI transaction reference number is 900112233445.

    Note: "Rs. " has a space after it (unlike the outbound alerts "Rs.951").
    Note: Account number is masked with "**" before the last 4 digits.
    """

    # Regex groups: (amount, acct_last4, vpa, sender_name, date_str)
    _UPI_INBOUND_PATTERN = re.compile(
        r"Rs\.\s*(\d[\d,]*(?:\.\d+)?)"         # amount — note \s* for "Rs. 950"
        r"\s+is successfully credited to your account\s+\*+(\d{4})"  # "**3703"
        r"\s+by VPA\s+(\S+)"                    # VPA (no spaces)
        r"\s+(.+?)"                             # sender name (non-greedy)
        r"\s+on\s+(\d{2}-\d{2}-\d{2})",        # "02-02-26"
        re.IGNORECASE | re.DOTALL,
    )

    _REF_PATTERN = re.compile(
        r"UPI transaction reference number is\s+(\d+)",
        re.IGNORECASE,
    )

    def can_parse(self, sender: str, subject: str) -> bool:
        return "account update for your hdfc bank" in subject.lower()

    def parse(self, html_body: str, received_date: datetime.date) -> list[ParsedTransaction]:
        text = _extract_hdfc_body_text(html_body)
        m = self._UPI_INBOUND_PATTERN.search(text)

        if not m:
            # Not a UPI inbound — could be E-mandate (no amount), card settings, etc.
            # All of these are valid "Account update" emails that we intentionally skip.
            logger.debug(
                "HDFCAccountUpdateParser: no UPI inbound pattern — "
                "likely an E-mandate or non-transaction notification, skipping."
            )
            return []

        amount_str, acct_last4, vpa, sender_name, date_str = m.groups()
        amount = Decimal(amount_str.replace(",", ""))

        txn_date = _parse_ddmmyy(date_str)
        if txn_date is None:
            logger.warning(
                "HDFCAccountUpdateParser: unrecognised date '%s', falling back to %s",
                date_str,
                received_date,
            )
            txn_date = received_date

        account_info = self._lookup_account(acct_last4)
        if account_info is None:
            return []
        account_id, source_key = account_info

        ref_m = self._REF_PATTERN.search(text)
        ref_number = ref_m.group(1) if ref_m else None

        sender_name = " ".join(sender_name.split())

        return [
            ParsedTransaction(
                txn_date=txn_date,
                # Direction is INFLOW → credit_amount > 0
                raw_description=f"UPI: {vpa} {sender_name}",
                debit_amount=Decimal("0"),
                credit_amount=amount,
                ref_number=ref_number,
                metadata={
                    "account_id": account_id,
                    "source_key": source_key,
                    "channel_hint": "UPI",
                    "vpa": vpa,
                    "email_source": "hdfc_upi_inbound",
                },
            )
        ]
