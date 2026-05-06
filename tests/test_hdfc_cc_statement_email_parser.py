"""Routing tests for :class:`parsers.statements.hdfc_cc.HDFCCCStatementEmailParser`."""

from __future__ import annotations

import pytest

from tests.email_parser_test_accounts import HDFC_CC_STATEMENT_ACCOUNTS
from parsers.statements.hdfc_cc import HDFCCCStatementEmailParser


@pytest.fixture
def parser() -> HDFCCCStatementEmailParser:
    accounts = HDFC_CC_STATEMENT_ACCOUNTS
    return HDFCCCStatementEmailParser(accounts)


def test_can_parse_swiggy_statement(parser: HDFCCCStatementEmailParser) -> None:
    assert parser.can_parse(
        "emailstatements.cards@hdfcbank.bank.in",
        "Your HDFC Bank - Swiggy HDFC Bank Credit Card Statement - March-2026",
    )


def test_can_parse_diners_privilege(parser: HDFCCCStatementEmailParser) -> None:
    assert parser.can_parse(
        "emailstatements.cards@hdfcbank.net",
        "Your HDFC Bank - Diners Privilege Credit Card Statement - March-2026",
    )


def test_can_parse_diners_club_international(parser: HDFCCCStatementEmailParser) -> None:
    assert parser.can_parse(
        "emailstatements.cards@hdfcbank.net",
        "Your HDFC Bank - Diners Club International Credit Card Statement - Jan-2025",
    )


def test_rejects_combined_savings_statement(parser: HDFCCCStatementEmailParser) -> None:
    assert not parser.can_parse(
        "alerts@hdfcbank.net",
        "HDFC Bank Combined Email Statement for …",
    )


def test_rejects_instalert(parser: HDFCCCStatementEmailParser) -> None:
    assert not parser.can_parse(
        "emailstatements.cards@hdfcbank.net",
        "Alert : Rs 500.00 debited from your HDFC Bank XX1905",
    )
