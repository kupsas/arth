"""
Email parser registry.

EMAIL_PARSER_REGISTRY maps each bank sender address to an ordered list of
parser instances. The router tries each parser's can_parse() in order and
uses the first one that returns True.

Order matters: put more specific subject patterns before catch-all ones.
For HDFC, HDFCAccountUpdateParser is last because its subject "Account update"
is the most generic — CC and UPI alerts have more specific subject lines.
"""

from scraper.config import BANK_SENDERS
from scraper.email_parsers.base import BaseEmailParser
from scraper.email_parsers.hdfc_bank import (
    HDFCAccountUpdateParser,
    HDFCCreditCardAlertParser,
    HDFCUPIAlertParser,
)
from scraper.email_parsers.icici_bank import ICICINetBankingParser


def _hdfc_parser_list(accounts: dict) -> list[BaseEmailParser]:
    """Fresh parser instances per sender address (shared `accounts` dict is OK)."""
    return [
        HDFCCreditCardAlertParser(accounts),
        HDFCUPIAlertParser(accounts),
        HDFCAccountUpdateParser(accounts),
    ]


# Each sender maps to a list of parsers tried in order.
# Only the FIRST matching parser is used per email.
_hdfc_accounts = BANK_SENDERS["alerts@hdfcbank.net"]["accounts"]

EMAIL_PARSER_REGISTRY: dict[str, list[BaseEmailParser]] = {
    "alerts@hdfcbank.net": _hdfc_parser_list(_hdfc_accounts),
    # Same InstaAlerts as .net — Gmail often shows From: HDFC Bank InstaAlerts <...@hdfcbank.bank.in>
    "alerts@hdfcbank.bank.in": _hdfc_parser_list(_hdfc_accounts),
    "customernotification@icici.bank.in": [
        ICICINetBankingParser(
            BANK_SENDERS["customernotification@icici.bank.in"]["accounts"]
        ),
    ],
}

__all__ = ["EMAIL_PARSER_REGISTRY"]
