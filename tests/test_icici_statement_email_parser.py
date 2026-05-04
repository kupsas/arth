"""Unit tests for :class:`scraper.email_parsers.icici_statement.ICICIStatementEmailParser` routing."""

from __future__ import annotations

import pytest

from tests.email_parser_test_accounts import ICICI_STATEMENT_ACCOUNTS
from scraper.email_parsers.icici_statement import ICICIStatementEmailParser


@pytest.fixture
def parser() -> ICICIStatementEmailParser:
    accounts = ICICI_STATEMENT_ACCOUNTS
    return ICICIStatementEmailParser(accounts)


def test_can_parse_annual_customernotification(parser: ICICIStatementEmailParser) -> None:
    assert parser.can_parse(
        "customernotification@icicibank.com",
        "Bank Statement from 01-01-2025 to 31-12-2025 for JOHN DOE",
    )


def test_can_parse_annual_any_sender_subject_only(parser: ICICIStatementEmailParser) -> None:
    """FY PDFs may arrive from new domains — routing uses subject line, not From:."""
    assert parser.can_parse(
        "annualfy@icici.bank.in",
        "Bank Statement from 01-01-2025 to 31-12-2025 for JOHN DOE",
    )

def test_can_parse_current_monthly_estatement(parser: ICICIStatementEmailParser) -> None:
    assert parser.can_parse(
        "estatement@icicibank.com",
        "ICICI Bank Statement from 01-03-2025 to 31-03-2025 for ...",
    )


def test_can_parse_current_monthly_estatement_bank_in(parser: ICICIStatementEmailParser) -> None:
    assert parser.can_parse(
        "estatement@icici.bank.in",
        "ICICI Bank Statement from 01-03-2025 to 31-03-2025 for ...",
    )


def test_can_parse_legacy_monthly_estatement(parser: ICICIStatementEmailParser) -> None:
    assert parser.can_parse(
        "estatement@icicibank.com",
        "Your Bank Statement for the period 01-08-2019 to 31-08-2019",
    )


def test_can_parse_legacy_monthly_estatement_bank_in(parser: ICICIStatementEmailParser) -> None:
    assert parser.can_parse(
        "estatement@icici.bank.in",
        "Your Bank Statement for the period 01-08-2019 to 31-08-2019",
    )


def test_rejects_imps_subject(parser: ICICIStatementEmailParser) -> None:
    assert not parser.can_parse(
        "customernotification@icici.bank.in",
        "IMPS transaction through ICICI Bank iMobile.",
    )


def test_rejects_random_marketing(parser: ICICIStatementEmailParser) -> None:
    assert not parser.can_parse(
        "customernotification@icicibank.com",
        "Important information about your ICICI Bank relationship",
    )
