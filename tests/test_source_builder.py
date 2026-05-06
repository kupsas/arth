"""Tests for :mod:`scraper.source_builder` last-4 inference (persist-sources heuristics)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from api.models import ScraperAccountMapping, UserPipelineSource
from scraper.source_builder import (
    _infer_accounts_dict,
    discovery_has_non_nse_broker_mail,
    filter_redundant_nse_broker_sources,
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
        session.commit()
        yield session, uid


def _read_fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / "email_samples" / name).read_text(encoding="utf-8")


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


def test_hdfc_cc_statement_swiggy_subject_maps_last4(infer_session: tuple[Session, str]) -> None:
    session, uid = infer_session
    cfg = {"parser_key": "hdfc_cc_statement"}
    subj = "Your HDFC Bank Credit Card statement (Swiggy HDFC Bank Credit Card)"
    acct = _infer_accounts_dict(
        cfg, [subj, "<html><body>PDF in attachment</body></html>"], session=session, user_id=uid
    )
    assert "1905" in acct


def test_hdfc_cc_statement_swiggy_subject_prefixed_like_gmail_fetch_maps_last4(
    infer_session: tuple[Session, str],
) -> None:
    """Production persist-sources prefixes ``Subject:`` onto the HTML body before inference."""
    session, uid = infer_session
    cfg = {"parser_key": "hdfc_cc_statement"}
    subj = "Your HDFC Bank Credit Card statement (Swiggy HDFC Bank Credit Card)"
    combined = f"Subject: {subj}\n\n<html><body>PDF in attachment</body></html>"
    acct = _infer_accounts_dict(cfg, [combined], session=session, user_id=uid)
    assert "1905" in acct


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
