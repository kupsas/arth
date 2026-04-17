"""
ICICI PPF ledger from a **combined** account PDF (annual or monthly email).

When page 1 stacks a **PPF** transaction table above **Savings**, we parse the PPF
vertical band only (via :mod:`pipeline.parsers.icici_savings` word layout), while
:class:`~pipeline.parsers.icici_savings.ICICISavingsParser` parses the savings band.
Rows are mapped to :class:`ParsedInvestmentTxn` using the same rules as :func:`parse_icici_ppf_csv`.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pdfplumber

from pipeline.holding_parsers.base import ParsedHolding, ParsedInvestmentTxn, parse_indian_amount
from pipeline.holding_parsers.icici_ppf import PPF_RATE_ANNUAL_DEFAULT
from pipeline.models import AssetClass, CompoundingFrequency, InvestmentTxnType, LiquidityClass, ValuationMethod
from pipeline.models import ParsedTransaction
from pipeline.parsers.icici_savings import (
    ICICISavingsParser,
    combined_ppf_y_window_page1,
)
from pipeline.ppf_maturity import ppf_statutory_maturity_date


def _utc_today() -> date:
    return datetime.now(UTC).date()


def parse_icici_ppf_from_combined_pdf(
    pdf_path: str | Path,
    *,
    account_platform: str = "ICICI PPF",
    reference_date: date | None = None,
    source_label: str = "icici_combined_pdf",
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    """Extract PPF rows from page 1 of a **combined** ICICI PDF (annual or monthly email).

    Works when the first page stacks a PPF transaction table above a Savings table.
    Savings rows are **not** returned here — use :class:`~pipeline.parsers.icici_savings.ICICISavingsParser`
    on the same file (it automatically restricts to the Savings band when both exist).

    Returns empty lists if there is no detectable PPF band (e.g. savings-only statement).
    """
    path = Path(pdf_path)
    ref = reference_date if reference_date is not None else _utc_today()

    with pdfplumber.open(path) as pdf:
        if not pdf.pages:
            return [], []
        page0 = pdf.pages[0]
        t0 = page0.extract_text() or ""
        if not _pdf_text_has_ppf_table_section(t0):
            return [], []
        win = combined_ppf_y_window_page1(page0)
        if win is None:
            return [], []
        y_lo, y_hi = win
        parser = ICICISavingsParser()
        pt_rows = parser._parse_page_combined(  # noqa: SLF001 — band parse shares classifier
            page0,
            1,
            y_min=y_lo,
            y_max=y_hi,
        )

    summary_bal = _ppf_balance_from_page_summary(t0)

    return _parsed_transactions_to_ppf_outputs(
        pt_rows,
        account_platform=account_platform,
        reference_date=ref,
        source_label=source_label,
        summary_balance=summary_bal,
    )


def _pdf_text_has_ppf_table_section(text: str) -> bool:
    """Enough signal to run the PPF y-band extractor (annual summary and/or monthly header)."""
    if "Statement of Transactions in PPF Account" in text:
        return True
    return "PPF A/c" in text and "Statement of Transactions in Account Number:" in text


def parse_icici_ppf_from_annual_pdf(
    pdf_path: str | Path,
    *,
    account_platform: str = "ICICI PPF",
    reference_date: date | None = None,
    source_label: str = "icici_annual_pdf",
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    """Backward-compatible alias for :func:`parse_icici_ppf_from_combined_pdf`."""
    return parse_icici_ppf_from_combined_pdf(
        pdf_path,
        account_platform=account_platform,
        reference_date=reference_date,
        source_label=source_label,
    )


def _ppf_balance_from_page_summary(text: str) -> float | None:
    """Closing balance from the 'PPF A/c … amount' summary row on page 1."""
    m = re.search(r"PPF\s+A/c\s+\S+\s+([\d,]+\.\d{2})", text)
    if not m:
        return None
    return parse_indian_amount(m.group(1))


def _parsed_transactions_to_ppf_outputs(
    rows: list[ParsedTransaction],
    *,
    account_platform: str,
    reference_date: date,
    source_label: str,
    summary_balance: float | None,
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    """Map savings-shaped :class:`ParsedTransaction` rows to PPF investment rows."""
    txns: list[ParsedInvestmentTxn] = []

    for p in sorted(rows, key=lambda x: x.txn_date):
        if p.txn_date > reference_date:
            continue
        desc = p.raw_description.strip()
        desc_l = desc.lower()
        if "opening" in desc_l and p.credit_amount > 0:
            continue
        dep = float(p.credit_amount)
        wdr = float(p.debit_amount)

        is_interest = "int.pd" in desc_l or "interest" in desc_l
        if dep > 0 and not is_interest:
            txns.append(
                ParsedInvestmentTxn(
                    txn_date=p.txn_date,
                    symbol=None,
                    name="PPF contribution",
                    txn_type=InvestmentTxnType.BUY.value,
                    quantity=1.0,
                    price_per_unit=dep,
                    total_amount=dep,
                    account_platform=account_platform,
                    notes=desc or None,
                    metadata={"source_file": source_label},
                )
            )
        elif is_interest and dep > 0:
            txns.append(
                ParsedInvestmentTxn(
                    txn_date=p.txn_date,
                    symbol=None,
                    name="PPF interest",
                    txn_type=InvestmentTxnType.DIVIDEND.value,
                    quantity=1.0,
                    price_per_unit=dep,
                    total_amount=dep,
                    account_platform=account_platform,
                    notes=desc or None,
                    metadata={"source_file": source_label},
                )
            )
        elif wdr > 0:
            txns.append(
                ParsedInvestmentTxn(
                    txn_date=p.txn_date,
                    symbol=None,
                    name="PPF withdrawal",
                    txn_type=InvestmentTxnType.SELL.value,
                    quantity=1.0,
                    price_per_unit=wdr,
                    total_amount=wdr,
                    account_platform=account_platform,
                    notes=desc or None,
                    metadata={"source_file": source_label},
                )
            )

    buy_sum = sum(t.total_amount for t in txns if t.txn_type == InvestmentTxnType.BUY.value)
    sell_sum = sum(t.total_amount for t in txns if t.txn_type == InvestmentTxnType.SELL.value)
    net_principal = round(buy_sum - sell_sum, 2)
    principal = net_principal if net_principal > 0 else None

    first_buy = next(
        (t.txn_date for t in txns if t.txn_type == InvestmentTxnType.BUY.value),
        None,
    )
    ppf_maturity = ppf_statutory_maturity_date(first_buy) if first_buy else None

    holding = ParsedHolding(
        symbol=None,
        name="Public Provident Fund (PPF)",
        quantity=None,
        asset_class=AssetClass.PPF.value,
        valuation_method=ValuationMethod.FIXED_RETURN.value,
        account_platform=account_platform,
        current_value=summary_balance,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        principal_amount=principal,
        interest_rate=PPF_RATE_ANNUAL_DEFAULT,
        compounding_frequency=CompoundingFrequency.ANNUALLY.value,
        maturity_date=ppf_maturity,
        metadata={"source_file": source_label},
    )
    return [holding], txns
