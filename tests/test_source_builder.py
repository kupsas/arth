"""Tests for :mod:`scraper.source_builder` last-4 inference (persist-sources heuristics)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from api.models import ScraperAccountMapping, Transaction, UserPipelineSource
from scraper.source_builder import (
    _PDF_ONLY_SELF_MAPPING_PARSERS,
    _infer_accounts_dict,
    discovery_has_non_nse_broker_mail,
    filter_redundant_nse_broker_sources,
    sync_user_pipeline_sources_from_scraper_mappings,
    sync_user_pipeline_sources_from_transactions,
)


@pytest.fixture
def infer_session(monkeypatch: pytest.MonkeyPatch) -> tuple[Session, str]:
    """Fresh SQLite (APP_ENV=test) session + stable user id for resolver-backed inference."""
    monkeypatch.setenv("APP_ENV", "test")
    from api.database import get_engine, init_db

    init_db()
    uid = "_test_source_builder_infer"
    engine = get_engine()
    with Session(engine) as session:
        for m in session.exec(select(ScraperAccountMapping).where(ScraperAccountMapping.user_id == uid)).all():
            session.delete(m)
        for m in session.exec(select(UserPipelineSource).where(UserPipelineSource.user_id == uid)).all():
            session.delete(m)
        for m in session.exec(select(Transaction).where(Transaction.user_id == uid)).all():
            session.delete(m)
        session.commit()
        yield session, uid


def _read_fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / "email_samples" / name).read_text(encoding="utf-8")


def test_sync_pipeline_sources_from_scraper_mappings_inserts_hdfc_savings(
    infer_session: tuple[Session, str],
) -> None:
    """Email discovery writes scraper mappings; uploads need matching ``UserPipelineSource``."""
    session, uid = infer_session
    session.add(
        ScraperAccountMapping(
            user_id=uid,
            sender_email="alerts@hdfcbank.net",
            last_4_digits="3703",
            account_id="HDFC_SAL_3703",
            source_key="hdfc_savings",
        )
    )
    session.commit()
    assert sync_user_pipeline_sources_from_scraper_mappings(session, uid) == 1
    session.commit()
    row = session.exec(
        select(UserPipelineSource).where(
            UserPipelineSource.user_id == uid,
            UserPipelineSource.source_key == "hdfc_savings",
        )
    ).first()
    assert row is not None
    assert row.account_id == "HDFC_SAL_3703"
    assert sync_user_pipeline_sources_from_scraper_mappings(session, uid) == 0


def test_hdfc_bank_html_upi_outbound_finds_account_last4(infer_session: tuple[Session, str]) -> None:
    session, uid = infer_session
    html = _read_fixture("alerts_hdfcbank_net_01.html")
    cfg = {"parser_key": "hdfc_bank"}
    acct = _infer_accounts_dict(cfg, ["❗ You have done a UPI txn", html], session=session, user_id=uid)
    assert "3703" in acct
    assert acct["3703"]["source_key"] == "hdfc_savings"


def test_hdfc_bank_credit_card_ending_plain_digits(infer_session: tuple[Session, str]) -> None:
    session, uid = infer_session
    body = (
        "We would like to inform you that Rs. 100.00 has been debited from "
        "your HDFC Bank Credit Card ending 1905 towards EXAMPLE on 14 Mar, 2026 at 12:00:00."
    )
    cfg = {"parser_key": "hdfc_bank"}
    acct = _infer_accounts_dict(
        cfg, ["A payment was made using your Credit Card", body], session=session, user_id=uid
    )
    assert "1905" in acct
    assert acct["1905"]["source_key"] == "hdfc_cc_1905"


def test_hdfc_bank_savings_last4_not_cc_when_generic_credit_card_mention_in_blob(
    infer_session: tuple[Session, str],
) -> None:
    """Regression: blob-wide "credit card" substring used to mis-tag savings tails as ``hdfc_cc_*``."""
    session, uid = infer_session
    html = _read_fixture("alerts_hdfcbank_net_01.html")
    marketing = " Earn more rewards on every credit card spend. Visit us today. "
    cfg = {"parser_key": "hdfc_bank"}
    acct = _infer_accounts_dict(cfg, ["HDFC Bank Alerts", html + marketing], session=session, user_id=uid)
    assert "3703" in acct
    assert acct["3703"]["source_key"] == "hdfc_savings"


def test_icici_bank_imps_body_finds_xxxx_last4(infer_session: tuple[Session, str]) -> None:
    session, uid = infer_session
    body = (
        "You have made an online IMPS payment of Rs. 1.00 towards SOMEONE "
        "on Mar 19, 2026 at 12:07 a.m. from your ICICI Bank Savings Account XXXX6118. "
        "The Transaction ID is 607800230914."
    )
    cfg = {"parser_key": "icici_bank"}
    acct = _infer_accounts_dict(
        cfg, ["IMPS transaction through ICICI Bank iMobile.", body], session=session, user_id=uid
    )
    assert "6118" in acct
    assert acct["6118"]["source_key"] == "icici_savings"


def test_icici_bank_alert_without_savings_word_still_finds_last4(infer_session: tuple[Session, str]) -> None:
    """Some ICICI templates say “ICICI Bank Account XXXX…” without the word Savings."""
    session, uid = infer_session
    body = (
        "Amount credited to your ICICI Bank Account XXXX6118 on 01-Jan-2026. "
        "Reference ID 123456."
    )
    cfg = {"parser_key": "icici_bank"}
    acct = _infer_accounts_dict(cfg, ["Credit notification", body], session=session, user_id=uid)
    assert "6118" in acct


def test_icici_xx118_resolves_via_user_pipeline_source(infer_session: tuple[Session, str]) -> None:
    """ICICI often shows XX118 — last three digits of last-four 6118."""
    session, uid = infer_session
    session.add(
        UserPipelineSource(
            user_id=uid,
            source_key="icici_savings",
            account_id="ICICI_SAV_6118",
            currency="INR",
            statement_folder="ICICI_Savings",
        )
    )
    session.commit()
    body = "Your ICICI Bank Account XX118 has been credited with Rs. 100.00"
    cfg = {"parser_key": "icici_bank"}
    acct = _infer_accounts_dict(cfg, ["Credit notification", body], session=session, user_id=uid)
    assert "6118" in acct


def test_icici_xxxx118_resolves_via_blob_when_full_last4_appears(infer_session: tuple[Session, str]) -> None:
    session, uid = infer_session
    body = (
        "from your ICICI Bank Savings Account XXXX118 for the above transaction. "
        "Reference: savings account 6118."
    )
    cfg = {"parser_key": "icici_bank"}
    acct = _infer_accounts_dict(cfg, ["Alert", body], session=session, user_id=uid)
    assert "6118" in acct


def test_icici_statement_pdf_shell_reuses_icici_savings_mappings(infer_session: tuple[Session, str]) -> None:
    """E-statement emails often have no account digits in HTML — reuse alert-derived mappings."""
    session, uid = infer_session
    session.add(
        ScraperAccountMapping(
            user_id=uid,
            sender_email="customernotification@icici.bank.in",
            last_4_digits="6118",
            account_id="ICICI_SAV_6118",
            source_key="icici_savings",
        )
    )
    session.commit()
    cfg = {"parser_key": "icici_statement"}
    shell = "Subject: Your ICICI Bank Account e-statement\n\n<html><body>See PDF attachment.</body></html>"
    acct = _infer_accounts_dict(cfg, [shell], session=session, user_id=uid)
    assert "6118" in acct
    assert acct["6118"]["source_key"] == "icici_savings"


def test_icici_direct_statement_uses_template_placeholder_accounts(infer_session: tuple[Session, str]) -> None:
    session, uid = infer_session
    cfg = {
        "parser_key": "icici_direct_statement",
        "accounts": {
            "0000": {"account_id": "ICICI_DIRECT", "source_key": "icici_direct_statement"},
        },
    }
    acct = _infer_accounts_dict(
        cfg,
        ["Subject: Equity statement\n\n<html><body>PDF attached</body></html>"],
        session=session,
        user_id=uid,
    )
    assert "0000" in acct
    assert acct["0000"]["account_id"] == "ICICI_DIRECT"
    assert acct["0000"]["source_key"] == "icici_direct_statement"


def test_hdfc_cc_statement_swiggy_subject_does_not_hardcode_last4(
    infer_session: tuple[Session, str],
) -> None:
    """Swiggy product name in subject must not map to a repo-owner card tail."""
    session, uid = infer_session
    cfg = {"parser_key": "hdfc_cc_statement"}
    subj = "Your HDFC Bank Credit Card statement (Swiggy HDFC Bank Credit Card)"
    acct = _infer_accounts_dict(
        cfg, [subj, "<html><body>PDF in attachment</body></html>"], session=session, user_id=uid
    )
    assert acct == {}


def test_hdfc_cc_statement_swiggy_subject_prefixed_like_gmail_fetch_does_not_hardcode_last4(
    infer_session: tuple[Session, str],
) -> None:
    """Production persist-sources prefixes ``Subject:`` onto the HTML body before inference."""
    session, uid = infer_session
    cfg = {"parser_key": "hdfc_cc_statement"}
    subj = "Your HDFC Bank Credit Card statement (Swiggy HDFC Bank Credit Card)"
    combined = f"Subject: {subj}\n\n<html><body>PDF in attachment</body></html>"
    acct = _infer_accounts_dict(cfg, [combined], session=session, user_id=uid)
    assert acct == {}


def test_hdfc_inbound_fixture_masked_account(infer_session: tuple[Session, str]) -> None:
    session, uid = infer_session
    html = _read_fixture("hdfc_upi_inbound_02.html")
    cfg = {"parser_key": "hdfc_bank"}
    acct = _infer_accounts_dict(cfg, ["Account update for your HDFC Bank A/c", html], session=session, user_id=uid)
    assert "3703" in acct


def test_filter_redundant_nse_keeps_nse_when_only_nse_broker() -> None:
    sources = [
        {"sender_email": "ebix@nse.co.in", "email_count_estimate": 5, "instrument_type": "broker"},
    ]
    assert discovery_has_non_nse_broker_mail(sources) is False
    out = filter_redundant_nse_broker_sources(sources)
    assert out is sources
    assert len(out) == 1


def test_filter_redundant_nse_drops_nse_when_icici_broker_present() -> None:
    """ICICI Direct mail makes NSE trade confirmations redundant (same trades)."""
    sources = [
        {
            "sender_email": "service@icicisecurities.com",
            "email_count_estimate": 12,
            "instrument_type": "broker",
        },
        {"sender_email": "ebix@nse.co.in", "email_count_estimate": 100, "instrument_type": "broker"},
        {"sender_email": "nseinvest@nse.co.in", "email_count_estimate": 39, "instrument_type": "broker"},
    ]
    assert discovery_has_non_nse_broker_mail(sources) is True
    out = filter_redundant_nse_broker_sources(sources)
    assert len(out) == 1
    assert out[0]["sender_email"] == "service@icicisecurities.com"


def test_filter_redundant_nse_keeps_both_when_icici_broker_has_zero_mail() -> None:
    """If the primary broker row matched no messages, NSE remains the usable feed."""
    sources = [
        {
            "sender_email": "service@icicisecurities.com",
            "email_count_estimate": 0,
            "instrument_type": "broker",
        },
        {"sender_email": "ebix@nse.co.in", "email_count_estimate": 10, "instrument_type": "broker"},
    ]
    assert discovery_has_non_nse_broker_mail(sources) is False
    out = filter_redundant_nse_broker_sources(sources)
    assert out is sources
    assert len(out) == 2


def test_sync_pipeline_sources_from_transactions_inserts_hdfc_savings(
    infer_session: tuple[Session, str],
) -> None:
    """When only ``Transaction`` rows exist (no scraper mapping), uploads still get a pipeline source."""
    import datetime
    import hashlib

    session, uid = infer_session
    h = hashlib.sha256(f"{uid}-txn-sync-1".encode()).hexdigest()
    session.add(
        Transaction(
            content_hash=h,
            txn_date=datetime.date(2025, 1, 1),
            account_id="HDFC_SAL_3703",
            user_id=uid,
            source_statement="email",
            direction="INFLOW",
            amount=1.0,
            raw_description="test",
            source_type="email",
        )
    )
    session.commit()
    assert sync_user_pipeline_sources_from_transactions(session, uid) == 1
    session.commit()
    row = session.exec(
        select(UserPipelineSource).where(
            UserPipelineSource.user_id == uid,
            UserPipelineSource.source_key == "hdfc_savings",
        )
    ).first()
    assert row is not None
    assert row.account_id == "HDFC_SAL_3703"
    assert sync_user_pipeline_sources_from_transactions(session, uid) == 0


def test_sync_pipeline_sources_second_hdfc_account_gets_suffix_key(
    infer_session: tuple[Session, str],
) -> None:
    import datetime
    import hashlib

    session, uid = infer_session
    session.add(
        UserPipelineSource(
            user_id=uid,
            source_key="hdfc_savings",
            account_id="HDFC_SAL_1111",
            currency="INR",
            statement_folder=None,
        )
    )
    h = hashlib.sha256(f"{uid}-txn-sync-2".encode()).hexdigest()
    session.add(
        Transaction(
            content_hash=h,
            txn_date=datetime.date(2025, 1, 2),
            account_id="HDFC_SAL_2222",
            user_id=uid,
            source_statement="email",
            direction="INFLOW",
            amount=2.0,
            raw_description="test2",
            source_type="email",
        )
    )
    session.commit()
    assert sync_user_pipeline_sources_from_transactions(session, uid) == 1
    session.commit()
    row = session.exec(
        select(UserPipelineSource).where(
            UserPipelineSource.user_id == uid,
            UserPipelineSource.account_id == "HDFC_SAL_2222",
        )
    ).first()
    assert row is not None
    assert row.source_key == "hdfc_savings_2222"


def test_pdf_only_self_mapping_includes_sbi_statement() -> None:
    assert "sbi_statement" in _PDF_ONLY_SELF_MAPPING_PARSERS


def test_ordered_backfill_sources_emits_sbi_savings_for_empty_accounts() -> None:
    from api.routes.onboarding import _ordered_backfill_sources

    bank = {
        "cbssbi.cas@alerts.sbi.bank.in": {
            "parser_key": "sbi_statement",
            "instrument_type": "savings",
            "accounts": {},
        },
    }
    sources = _ordered_backfill_sources(bank)
    assert sources == [{"source_key": "sbi_savings", "instrument_type": "savings"}]
