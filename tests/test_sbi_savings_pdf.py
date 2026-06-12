"""SBI CAS savings transaction table parsing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from parsers.uploads.sbi_savings import SBISavingsParser

_REPO = Path(__file__).resolve().parents[1]
_SAMPLE_PDF = _REPO / "data" / "samples" / "sbi" / "eaccount_statement.pdf"


def test_classify_anchor_row() -> None:
    parser = SBISavingsParser()
    line_words = [
        {"x0": 19.0, "text": "01-04-26", "top": 100},
        {"x0": 62.0, "text": "UPI/DR/609170311459/Test", "top": 100},
        {"x0": 367.3, "text": "-", "top": 100},
        {"x0": 435.6, "text": "0", "top": 100},
        {"x0": 481.1, "text": "6300.00", "top": 100},
        {"x0": 542.2, "text": "355917.77", "top": 100},
    ]
    classified = parser._classify_line(line_words, " ".join(w["text"] for w in line_words))
    assert classified is not None
    kind, payload = classified
    assert kind == "anchor"
    assert payload["date"] == date(2026, 4, 1)
    assert payload["debit"] == Decimal("6300.00")
    assert payload["credit"] == Decimal("0")


def test_build_transactions_stitches_continuation_prefix() -> None:
    parser = SBISavingsParser()
    anchors = [
        {
            "y": 200.0,
            "date": date(2026, 4, 8),
            "inline_ref": "-",
            "credit": Decimal("0"),
            "debit": Decimal("90.00"),
            "balance": Decimal("336080.27"),
        }
    ]
    continuations = [
        {"y": 180.0, "text": "UPI/DR/609834184136/MR BONGO/YESB/q232636451/UPI"},
    ]
    rows = parser._build_transactions(anchors, continuations, "4399")
    assert len(rows) == 1
    assert "MR BONGO" in rows[0].raw_description
    assert rows[0].metadata["account_last4"] == "4399"


def test_clean_description_strips_balance_footer_tail() -> None:
    raw = "UPI/DR/415106555109/Salubris/YESB/paytmqr281/UPI Balance on 31-05-24: 335834.16"
    assert SBISavingsParser._clean_description(raw) == (
        "UPI/DR/415106555109/Salubris/YESB/paytmqr281/UPI"
    )


def test_skips_opening_balance_null_row() -> None:
    parser = SBISavingsParser()
    line_words = [
        {"x0": 12.0, "text": "Opening", "top": 50},
        {"x0": 105.8, "text": "01-04-26:", "top": 50},
        {"x0": 357.6, "text": "null", "top": 50},
    ]
    assert parser._classify_line(line_words, "Opening Balance on 01-04-26: null null") is None


@pytest.mark.skipif(not _SAMPLE_PDF.is_file(), reason="Place decrypted sample at data/samples/sbi/")
def test_parse_real_sample_pdf() -> None:
    rows = SBISavingsParser().parse(_SAMPLE_PDF)
    assert len(rows) >= 1
    assert all(r.txn_date.year >= 2000 for r in rows)
