"""Subject routing and password wiring for Zerodha demat statement emails."""

from __future__ import annotations

import json
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.models import PasswordTemplate, UserSecrets
from parsers.statements.zerodha_demat import (
    ZerodhaDematStatementEmailParser,
    classify_zerodha_demat_statement_subject,
)
from scraper.pdf_passwords import resolve_zerodha_demat_pdf_password_candidates
from scraper.secrets_context import statement_secrets_context


@pytest.fixture(name="engine")
def _engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(name="session")
def _session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def accounts() -> dict:
    return {"0000": {"account_id": "ZERODHA", "source_key": "zerodha_demat_statement"}}


@pytest.fixture
def parser(accounts: dict) -> ZerodhaDematStatementEmailParser:
    return ZerodhaDematStatementEmailParser(accounts)


def test_classify_production_subject() -> None:
    assert classify_zerodha_demat_statement_subject(
        "Zerodha Broking Ltd: Monthly Demat Transaction"
    )


def test_classify_forwarded_subject() -> None:
    assert classify_zerodha_demat_statement_subject(
        "Fwd: Zerodha Broking Ltd: Monthly Demat Transaction"
    )


def test_rejects_unrelated_subject(parser: ZerodhaDematStatementEmailParser) -> None:
    assert not parser.can_parse(
        "no-reply-transaction-with-holding-statement@reportsmailer.zerodha.net",
        "Your order was executed",
    )


def test_registry_lists_zerodha_parser() -> None:
    from parsers.email_registry import build_email_parser_registry

    reg = build_email_parser_registry()
    sender = "no-reply-transaction-with-holding-statement@reportsmailer.zerodha.net"
    parsers = reg[sender]
    assert len(parsers) == 1
    assert isinstance(parsers[0], ZerodhaDematStatementEmailParser)


def test_password_from_user_secrets_pan(session) -> None:
    session.add(
        PasswordTemplate(
            parser_key="zerodha_demat_statement",
            display_name="Zerodha demat",
            required_fields_json='["pan"]',
            password_formula="{pan}",
        )
    )
    session.add(
        UserSecrets(
            user_id="testuser",
            secrets_json=json.dumps({"ARTH_PDF_INGREDIENT_PAN": "CKNPB1603B"}),
        )
    )
    session.commit()
    with statement_secrets_context(session, "testuser"):
        cands = resolve_zerodha_demat_pdf_password_candidates()
    assert "CKNPB1603B" in cands


def test_bank_senders_includes_zerodha() -> None:
    from scraper.config import BANK_SENDERS

    key = "no-reply-transaction-with-holding-statement@reportsmailer.zerodha.net"
    assert key in BANK_SENDERS
    assert BANK_SENDERS[key]["parser_key"] == "zerodha_demat_statement"
