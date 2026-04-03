"""Unit tests for :mod:`pipeline.parsers.hdfc_cc_pdf` line parsing."""

from __future__ import annotations

from decimal import Decimal

from pipeline.parsers.hdfc_cc import HDFCCreditCardParser
from pipeline.parsers.hdfc_cc_pdf import _parse_one_line


def test_parse_domestic_purchase() -> None:
    h = HDFCCreditCardParser()
    line = "15/02/2026| 10:04 EMI GPAY UTILITIESMUMBAI C 2,640.30 l"
    pt = _parse_one_line(line, "domestic", h, desc_prefix="")
    assert pt is not None
    assert pt.txn_date.isoformat() == "2026-02-15"
    assert pt.debit_amount == Decimal("2640.30")
    assert pt.credit_amount == Decimal("0")
    assert pt.metadata.get("domestic_or_international") == "domestic"


def test_parse_credit_payment() -> None:
    h = HDFCCreditCardParser()
    line = (
        "01/03/2026| 13:47 CREDIT CARD PAYMENTNet Banking "
        "(Ref# 00000000000301021511961) + C 91,275.00 l"
    )
    pt = _parse_one_line(line, "domestic", h, desc_prefix="")
    assert pt is not None
    assert pt.credit_amount == Decimal("91275.00")
    assert pt.debit_amount == Decimal("0")


def test_parse_international_usd_inr() -> None:
    h = HDFCCreditCardParser()
    line = (
        "08/03/2026 | 18:25 CURSOR, AI POWERED IDENEW YORK "
        "USD 23.60 C 2,170.79 l"
    )
    from pipeline.parsers.hdfc_cc_pdf import _normalize_leading_pipe

    pt = _parse_one_line(
        _normalize_leading_pipe(line),
        "international",
        h,
        desc_prefix="",
    )
    assert pt is not None
    assert pt.debit_amount == Decimal("2170.79")
    assert pt.metadata.get("domestic_or_international") == "international"


def test_prefix_merges_payment_narration() -> None:
    h = HDFCCreditCardParser()
    line = "01/03/2026| 13:47 + C 39,042.00 l"
    pt = _parse_one_line(
        line,
        "domestic",
        h,
        desc_prefix="CREDIT CARD PAYMENTNet Banking (Ref#",
    )
    assert pt is not None
    assert "CREDIT CARD PAYMENT" in pt.raw_description
    assert pt.credit_amount == Decimal("39042.00")
