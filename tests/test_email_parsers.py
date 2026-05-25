"""
Unit tests for all four email alert parsers.

Each test loads HTML fixtures under ``tests/fixtures/email_samples/`` (minimal
synthetic bodies checked into git; replace via ``scripts/sync_email_parser_fixtures.py``
when you need fresher bank templates) and asserts parsers emit the expected
ParsedTransaction rows.

Fixture map (filenames kept for paths; ``hdfc_upi_inbound_*`` = HDFC *Account update* template):
    alerts_hdfcbank_net_01.html          → HDFCUPIAlertParser         (UPI outbound ₹951)
    alerts_hdfcbank_net_02..05.html      → HDFCCreditCardAlertParser  (CC swipes, card 1905, legacy subject)
    alerts_hdfcbank_net_06_…2026.html   → HDFCCreditCardAlertParser  (2026 "payment was made" + new body)
    hdfc_upi_inbound_01.html             → HDFCAccountUpdateParser    (e-mandate / NACH — not UPI txn → [])
    hdfc_upi_inbound_02.html             → HDFCAccountUpdateParser    (UPI inbound credit ₹950 — 1 txn)
    hdfc_upi_inbound_03.html             → HDFCAccountUpdateParser    (card settings — not UPI txn → [])
    icici_bank_in_01.html                → ICICINetBankingParser      (IMPS ₹1)
    icici_bank_in_02.html                → ICICINetBankingParser      (NEFT ₹1)
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.email_parser_test_accounts import HDFC_ALERT_ACCOUNTS, ICICI_INSTA_ACCOUNTS
from parsers.alerts.hdfc import (
    HDFCAccountUpdateParser,
    HDFCCreditCardAlertParser,
    HDFCUPIAlertParser,
)
from parsers.alerts.icici import ICICINetBankingParser

# ─── Shared constants ─────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures" / "email_samples"

# Parser instances — one per bank sender, built with the live config so account
# lookups (last-4 → account_id) use the same values as production.
HDFC_ACCTS = HDFC_ALERT_ACCOUNTS
ICICI_ACCTS = ICICI_INSTA_ACCOUNTS

HDFC_CC_PARSER     = HDFCCreditCardAlertParser(HDFC_ACCTS)
HDFC_UPI_PARSER    = HDFCUPIAlertParser(HDFC_ACCTS)
HDFC_ACCT_PARSER   = HDFCAccountUpdateParser(HDFC_ACCTS)
ICICI_PARSER       = ICICINetBankingParser(ICICI_ACCTS)

# A fixed "received date" passed to parse() as the fallback.  Actual dates are
# parsed from the email body, so this only matters for tests where we want to
# verify date-parsing resilience.
RECEIVED = datetime.date(2026, 3, 19)


def _html(filename: str) -> str:
    """Read a fixture HTML file by name."""
    return (FIXTURES / filename).read_text()


# ─── can_parse() — subject-routing correctness ────────────────────────────────

class TestCanParse:
    """
    can_parse() is the cheap first-pass filter: it checks the subject line
    without downloading the email body.  These tests verify each parser
    accepts its own subjects and rejects others.
    """

    HDFC   = "alerts@hdfcbank.net"
    ICICI  = "customernotification@icici.bank.in"

    def test_cc_parser_matches_cc_subject(self):
        assert HDFC_CC_PARSER.can_parse(self.HDFC, "Rs.1014.00 debited via Credit Card **1905")

    def test_cc_parser_matches_2026_payment_made_subject(self):
        # HDFC ~2026+ replaces "debited via Credit Card" in the subject
        assert HDFC_CC_PARSER.can_parse(
            self.HDFC,
            "A payment was made using your Credit Card",
        )

    def test_cc_parser_rejects_upi_subject(self):
        assert not HDFC_CC_PARSER.can_parse(self.HDFC, "❗  You have done a UPI txn. Check details!")

    def test_cc_parser_rejects_account_update_subject(self):
        assert not HDFC_CC_PARSER.can_parse(self.HDFC, "Account update for your HDFC Bank A/c")

    def test_upi_parser_matches_upi_subject(self):
        assert HDFC_UPI_PARSER.can_parse(self.HDFC, "❗  You have done a UPI txn. Check details!")

    def test_upi_parser_rejects_cc_subject(self):
        assert not HDFC_UPI_PARSER.can_parse(self.HDFC, "Rs.1014.00 debited via Credit Card **1905")

    def test_account_update_parser_matches_account_update_subject(self):
        assert HDFC_ACCT_PARSER.can_parse(self.HDFC, "Account update for your HDFC Bank A/c")

    def test_account_update_parser_rejects_upi_subject(self):
        assert not HDFC_ACCT_PARSER.can_parse(self.HDFC, "❗  You have done a UPI txn. Check details!")

    def test_icici_parser_matches_imps_subject(self):
        assert ICICI_PARSER.can_parse(self.ICICI, "IMPS transaction through ICICI Bank iMobile.")

    def test_icici_parser_matches_neft_subject(self):
        assert ICICI_PARSER.can_parse(self.ICICI, "NEFT transaction through ICICI Bank iMobile.")

    def test_icici_parser_rejects_mab_subject(self):
        # MAB reminders come from customercare@icicibank.com — a different sender
        # that the ICICINetBankingParser is not responsible for.
        assert not ICICI_PARSER.can_parse(
            "customercare@icicibank.com",
            "Important Update Monthly Average Balance Requirement",
        )


# ─── HDFCUPIAlertParser: alerts_hdfcbank_net_01.html ─────────────────────────

class TestHDFCUPIOutbound:
    """UPI outbound ₹951 from savings account 3703 to eatclub@icici."""

    def _parse(self):
        return HDFC_UPI_PARSER.parse(_html("alerts_hdfcbank_net_01.html"), RECEIVED)

    def test_returns_one_transaction(self):
        assert len(self._parse()) == 1

    def test_amount_is_debit(self):
        t = self._parse()[0]
        assert t.debit_amount  == Decimal("951.00")
        assert t.credit_amount == Decimal("0")

    def test_txn_date(self):
        # Date "15-03-26" in the email → 15 March 2026
        assert self._parse()[0].txn_date == datetime.date(2026, 3, 15)

    def test_raw_description(self):
        assert self._parse()[0].raw_description == "UPI: eatclub@icici EatClub"

    def test_ref_number(self):
        # UPI outbound emails include a reference number
        assert self._parse()[0].ref_number == "120080887305"

    def test_account_id_in_metadata(self):
        assert self._parse()[0].metadata["account_id"] == "HDFC_SAL_3703"

    def test_source_key_in_metadata(self):
        assert self._parse()[0].metadata["source_key"] == "hdfc_savings"

    def test_channel_hint_is_upi(self):
        assert self._parse()[0].metadata["channel_hint"] == "UPI"

    def test_vpa_in_metadata(self):
        assert self._parse()[0].metadata["vpa"] == "eatclub@icici"


class TestHDFCUPIOutbound2026Template:
    """May 2026+ template: greetings lead-in, ``is debited``, ``account ending``, ``towards VPA``, merchant in parens."""

    HTML = """<!DOCTYPE html><html><body>
<table><tr><td class="td esd-text">
Dear Customer, Greetings from HDFC Bank! Rs.299.00 is debited from your account ending 3703
towards VPA spotify.bdsi@hdfcbank (SPOTIFY INDIA PVT LTD) on 22-05-26.
Your UPI transaction reference number is 900112233445.
</td></tr></table></body></html>"""

    def test_parses_2026_shape(self):
        rows = HDFC_UPI_PARSER.parse(self.HTML, RECEIVED)
        assert len(rows) == 1
        t = rows[0]
        assert t.debit_amount == Decimal("299.00")
        assert t.txn_date == datetime.date(2026, 5, 22)
        assert t.raw_description == "UPI: spotify.bdsi@hdfcbank SPOTIFY INDIA PVT LTD"
        assert t.metadata["vpa"] == "spotify.bdsi@hdfcbank"
        assert t.ref_number == "900112233445"


class TestHDFCUPIOutboundLegacyMaskedAndNoMerchant:
    """~2023 template: ``account **3703``, VPA immediately before ``on`` (no merchant text)."""

    HTML_MASKED = """<!DOCTYPE html><html><body>
<table><tr><td class="td esd-text">
Dear Customer, Rs.4607.00 has been debited from account **3703 to VPA Q652095861@ybl on 09-09-23.
Your UPI transaction reference number is 325248523604.
</td></tr></table></body></html>"""

    def test_masked_source_account_parses(self):
        rows = HDFC_UPI_PARSER.parse(self.HTML_MASKED, RECEIVED)
        assert len(rows) == 1
        t = rows[0]
        assert t.debit_amount == Decimal("4607.00")
        assert t.txn_date == datetime.date(2023, 9, 9)
        assert t.raw_description == "UPI: Q652095861@ybl"
        assert t.metadata["vpa"] == "Q652095861@ybl"
        assert t.ref_number == "325248523604"


class TestHDFCUPIOutboundAccountToAccount:
    """Legacy transfer to another masked account (no VPA)."""

    HTML = """<!DOCTYPE html><html><body>
<table><tr><td class="td esd-text">
Dear Customer, Rs.2000.00 has been debited from account **3703 to account **4875 on 17-09-23.Your UPI transaction reference number is 326006863787.
</td></tr></table></body></html>"""

    def test_parses(self):
        rows = HDFC_UPI_PARSER.parse(self.HTML, RECEIVED)
        assert len(rows) == 1
        t = rows[0]
        assert t.debit_amount == Decimal("2000.00")
        assert t.txn_date == datetime.date(2023, 9, 17)
        assert t.raw_description == "UPI: to-acct **4875"
        assert t.metadata["vpa"] == "account-4875"
        assert t.ref_number == "326006863787"


# ─── HDFCCreditCardAlertParser: alerts_hdfcbank_net_02..05.html ───────────────

class TestHDFCCCAlert:
    """
    Four different CC swipe alerts on card 1905.  Parameterised so adding a new
    fixture file is a one-liner — just extend the params list.
    """

    @pytest.mark.parametrize("fname,amount,txn_date,desc", [
        (
            "alerts_hdfcbank_net_02.html",
            Decimal("1014.00"),
            datetime.date(2026, 3, 14),
            "CC: PYU*Swiggy Food",
        ),
        (
            "alerts_hdfcbank_net_03.html",
            Decimal("529.00"),
            datetime.date(2026, 3, 12),
            "CC: PYU*Swiggy Food",
        ),
        (
            "alerts_hdfcbank_net_04.html",
            Decimal("157.00"),
            datetime.date(2026, 2, 27),
            "CC: PYU*Swiggy Food",
        ),
        (
            "alerts_hdfcbank_net_05.html",
            Decimal("262.00"),
            datetime.date(2026, 2, 25),
            "CC: RSP*INSTAMART",
        ),
        (
            "alerts_hdfcbank_net_06_cc_payment_made_2026.html",
            Decimal("2209.81"),
            datetime.date(2026, 4, 22),
            "CC: CLAUDE.AI SUBSCRIPTION",
        ),
    ])
    def test_cc_swipe_fields(self, fname, amount, txn_date, desc):
        txns = HDFC_CC_PARSER.parse(_html(fname), RECEIVED)
        assert len(txns) == 1
        t = txns[0]
        assert t.debit_amount  == amount
        assert t.credit_amount == Decimal("0")
        assert t.txn_date      == txn_date
        assert t.raw_description == desc

    def test_account_id_for_card_1905(self):
        t = HDFC_CC_PARSER.parse(_html("alerts_hdfcbank_net_02.html"), RECEIVED)[0]
        assert t.metadata["account_id"] == "HDFC_CC_1905"

    def test_card_last4_in_metadata(self):
        t = HDFC_CC_PARSER.parse(_html("alerts_hdfcbank_net_02.html"), RECEIVED)[0]
        assert t.metadata["card_last4"] == "1905"

    def test_channel_hint_is_card(self):
        t = HDFC_CC_PARSER.parse(_html("alerts_hdfcbank_net_02.html"), RECEIVED)[0]
        assert t.metadata["channel_hint"] == "CARD"

    def test_cc_alert_has_no_ref_number(self):
        # CC swipe emails don't include a UPI-style reference number.
        t = HDFC_CC_PARSER.parse(_html("alerts_hdfcbank_net_02.html"), RECEIVED)[0]
        assert t.ref_number is None


# ─── HDFCAccountUpdateParser: e-mandate / NACH (hdfc_upi_inbound_01.html) ─────

class TestHDFCEmandateSkipped:
    """
    Same *subject* bucket as UPI inbound ("Account update for your HDFC Bank A/c") but
    this body is e-mandate / NACH registration — not a credited-to-savings UPI line.
    Parser must return [] (no crash).
    """

    def test_emandate_returns_empty_list(self):
        result = HDFC_ACCT_PARSER.parse(_html("hdfc_upi_inbound_01.html"), RECEIVED)
        assert result == []


# ─── HDFCAccountUpdateParser: UPI inbound (hdfc_upi_inbound_02.html) ──────────

class TestHDFCUPIInbound:
    """UPI inbound credit of ₹950 (synthetic VPA / payee labels in the fixture HTML)."""

    def _parse(self):
        return HDFC_ACCT_PARSER.parse(_html("hdfc_upi_inbound_02.html"), RECEIVED)

    def test_returns_one_transaction(self):
        assert len(self._parse()) == 1

    def test_amount_is_credit(self):
        # Inbound UPI → direction INFLOW → credit_amount > 0
        t = self._parse()[0]
        assert t.credit_amount == Decimal("950.00")
        assert t.debit_amount  == Decimal("0")

    def test_txn_date(self):
        # Date "02-02-26" in the email → 2 Feb 2026
        assert self._parse()[0].txn_date == datetime.date(2026, 2, 2)

    def test_raw_description(self):
        assert self._parse()[0].raw_description == "UPI: sender.demo@okhdfcbank EXAMPLE RECEIVER"

    def test_ref_number(self):
        assert self._parse()[0].ref_number == "900112233445"

    def test_account_id(self):
        assert self._parse()[0].metadata["account_id"] == "HDFC_SAL_3703"

    def test_email_source_hint(self):
        assert self._parse()[0].metadata["email_source"] == "hdfc_upi_inbound"


# ─── HDFCAccountUpdateParser: card settings (hdfc_upi_inbound_03.html) ────────

class TestHDFCCardSettingsSkipped:
    """
    Card / Visa settings emails use the same "Account update" subject as true UPI
    credits, but the body is account-service copy only — not an inbound UPI line.
    Parser returns [].
    """

    def test_card_settings_email_returns_empty_list(self):
        result = HDFC_ACCT_PARSER.parse(_html("hdfc_upi_inbound_03.html"), RECEIVED)
        assert result == []


# ─── ICICINetBankingParser: IMPS (icici_bank_in_01.html) ──────────────────────

class TestICICIIMPS:
    """IMPS outbound ₹1 via iMobile to SASHANK SAI KUPPA from account 6118."""

    def _parse(self):
        return ICICI_PARSER.parse(_html("icici_bank_in_01.html"), RECEIVED)

    def test_returns_one_transaction(self):
        assert len(self._parse()) == 1

    def test_amount_is_debit(self):
        t = self._parse()[0]
        assert t.debit_amount  == Decimal("1.00")
        assert t.credit_amount == Decimal("0")

    def test_txn_date(self):
        # "Mar 19, 2026" in the email body
        assert self._parse()[0].txn_date == datetime.date(2026, 3, 19)

    def test_raw_description(self):
        assert self._parse()[0].raw_description == "IMPS: SASHANK SAI KUPPA"

    def test_ref_number(self):
        assert self._parse()[0].ref_number == "607800230914"

    def test_account_id(self):
        assert self._parse()[0].metadata["account_id"] == "ICICI_SAV_6118"

    def test_source_key(self):
        assert self._parse()[0].metadata["source_key"] == "icici_savings"

    def test_txn_method_in_metadata(self):
        assert self._parse()[0].metadata["txn_method"] == "IMPS"

    def test_channel_hint_is_bank(self):
        assert self._parse()[0].metadata["channel_hint"] == "BANK"


# ─── ICICINetBankingParser: NEFT (icici_bank_in_02.html) ──────────────────────

class TestICICINEFT:
    """NEFT outbound ₹1 via iMobile.  Same template as IMPS — just different
    payment method and alphanumeric transaction ID format."""

    def _parse(self):
        return ICICI_PARSER.parse(_html("icici_bank_in_02.html"), RECEIVED)

    def test_returns_one_transaction(self):
        assert len(self._parse()) == 1

    def test_raw_description_contains_neft(self):
        assert self._parse()[0].raw_description == "NEFT: SASHANK SAI KUPPA"

    def test_neft_ref_number_format(self):
        # NEFT transaction IDs are alphanumeric with a prefix, e.g. "IN12607828774378"
        ref = self._parse()[0].ref_number
        assert ref == "IN12607828774378"
        assert ref.startswith("IN")  # NEFT prefix

    def test_txn_method_in_metadata(self):
        assert self._parse()[0].metadata["txn_method"] == "NEFT"

    def test_amount(self):
        assert self._parse()[0].debit_amount == Decimal("1.00")


def test_hdfc_alert_registry_merges_net_and_bank_in_account_maps() -> None:
    """Regression: mappings are often split — savings last-4 on .net, CC last-4 on .bank.in.

    ``build_email_parser_registry`` must union both so CC alerts from either domain resolve.
    """
    from parsers.email_registry import build_email_parser_registry

    bs = {
        "alerts@hdfcbank.net": {
            "accounts": {"3703": {"account_id": "HDFC_SAL_3703", "source_key": "hdfc_savings"}},
        },
        "alerts@hdfcbank.bank.in": {
            "accounts": {"1905": {"account_id": "HDFC_CC_1905", "source_key": "hdfc_cc"}},
        },
    }
    reg = build_email_parser_registry(bs)
    cc_bi = next(p for p in reg["alerts@hdfcbank.bank.in"] if isinstance(p, HDFCCreditCardAlertParser))
    cc_net = next(p for p in reg["alerts@hdfcbank.net"] if isinstance(p, HDFCCreditCardAlertParser))
    merged_keys = {"3703", "1905"}
    assert set(cc_bi.accounts.keys()) == merged_keys
    assert cc_net.accounts == cc_bi.accounts
