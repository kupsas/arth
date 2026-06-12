"""Subject routing and password wiring for SBI e-account statement emails."""

from __future__ import annotations

import json
import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode("ascii"))

from api.models import PasswordTemplate, UserSecrets
from parsers.statements.sbi import (
    SBIStatementEmailParser,
    classify_sbi_statement_subject,
)
from scraper.pdf_passwords import (
    ARTH_PDF_INGREDIENT_DOB_ISO,
    ARTH_PDF_INGREDIENT_SBI_MOBILE_LAST5,
    resolve_sbi_statement_pdf_password_candidates,
)
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
    return {"4399": {"account_id": "SBI_DAD", "source_key": "sbi_savings"}}


@pytest.fixture
def parser(accounts: dict) -> SBIStatementEmailParser:
    return SBIStatementEmailParser(accounts)


def test_classify_production_subject() -> None:
    assert classify_sbi_statement_subject(
        "E-account statement for your SBI account(s)."
    )


def test_classify_forwarded_subject() -> None:
    assert classify_sbi_statement_subject(
        "Fwd: E-account statement for your SBI account(s)."
    )


def test_rejects_unrelated_subject(parser: SBIStatementEmailParser) -> None:
    assert not parser.can_parse(
        "cbssbi.cas@alerts.sbi.bank.in",
        "CBSSBI ALERT",
    )


def test_registry_lists_sbi_parsers() -> None:
    from parsers.email_registry import build_email_parser_registry

    reg = build_email_parser_registry()
    for sender in (
        "cbssbi.cas@alerts.sbi.bank.in",
        "cbssbi.cas@alerts.sbi.co.in",
    ):
        parsers = reg[sender]
        assert len(parsers) == 1
        assert isinstance(parsers[0], SBIStatementEmailParser)


def test_bank_senders_includes_sbi() -> None:
    from scraper.config import BANK_SENDERS

    for sender in (
        "cbssbi.cas@alerts.sbi.bank.in",
        "cbssbi.cas@alerts.sbi.co.in",
    ):
        assert sender in BANK_SENDERS
        assert BANK_SENDERS[sender]["parser_key"] == "sbi_statement"


def test_password_from_user_secrets_mobile_and_dob(session) -> None:
    session.add(
        PasswordTemplate(
            parser_key="sbi_statement",
            display_name="SBI e-account",
            required_fields_json='["dob_iso", "sbi_mobile_last5"]',
            password_formula="{sbi_mobile_last5}{dob_ddmmyy}",
        )
    )
    session.add(
        UserSecrets(
            user_id="testuser",
            secrets_json=json.dumps(
                {
                    ARTH_PDF_INGREDIENT_SBI_MOBILE_LAST5: "98765",
                    ARTH_PDF_INGREDIENT_DOB_ISO: "1982-09-16",
                }
            ),
        )
    )
    session.commit()
    with statement_secrets_context(session, "testuser"):
        cands = resolve_sbi_statement_pdf_password_candidates()
    assert "98765160982" in cands
