"""
Liability seed parsers: bike loan key-value text; term insurance PDF (password).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pdfplumber

from parsers.holdings.base import ParsedLiability
from pipeline.models import LiabilityType


def parse_bike_loan_txt(path: Path) -> list[ParsedLiability]:
    """Parse simple ``Key: value`` lines (case-insensitive keys)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    kv: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        kv[k.strip().lower()] = v.strip()

    def num(s: str) -> float:
        return float(re.sub(r"[,\s]", "", s) or "0")

    product = kv.get("product", "Bike loan")
    loan_amt = num(kv.get("loan amount", kv.get("loan_amount", "0")))
    tenure_raw = kv.get("tenure", "0")
    tenure_m = 0
    m_t = re.search(r"(\d+)", tenure_raw)
    if m_t:
        tenure_m = int(m_t.group(1))
    emi = num(kv.get("emi", "0"))
    rate = num(kv.get("interest rate", kv.get("interest_rate", "0")))

    notes = f"Parsed from {path.name}. Update principal_outstanding after reconciling EMIs."
    return [
        ParsedLiability(
            name=product,
            liability_type=LiabilityType.SECURED_LOAN.value,
            principal_outstanding=loan_amt,
            interest_rate=rate,
            emi_amount=emi if emi else None,
            tenure_remaining_months=tenure_m if tenure_m else None,
            notes=notes,
        )
    ]


def _regex_money(m: re.Match[str] | None, group: int = 1) -> float:
    """Parse first numeric capture from a regex match; empty or missing → 0."""
    if not m:
        return 0.0
    raw = (m.group(group) or "").strip().replace(",", "")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def parse_term_insurance_pdf(path: Path, *, password: str | None = None) -> list[ParsedLiability]:
    """Best-effort text extraction; regex for premium and sum assured."""
    pwd = password or os.environ.get("PDF_DECRYPT_PASSWORD") or ""
    out: list[ParsedLiability] = []
    with pdfplumber.open(path, password=(pwd if pwd else None)) as pdf:
        full = "\n".join((p.extract_text() or "") for p in pdf.pages)

    name_m = re.search(r"(?:policy|plan)\s*[:\-]\s*(.+)", full, re.I)
    policy_name = name_m.group(1).strip()[:120] if name_m else "Term insurance"

    prem_m = re.search(
        r"(?:premium|installment)\s*[:\-]?\s*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)",
        full,
        re.I,
    )
    premium = _regex_money(prem_m)

    sum_m = re.search(
        r"(?:sum\s+assured|coverage)\s*[:\-]?\s*(?:Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)",
        full,
        re.I,
    )
    coverage = _regex_money(sum_m)

    notes = f"Parsed from {path.name}. Verify premium frequency and dates in the PDF."
    out.append(
        ParsedLiability(
            name=policy_name,
            liability_type=LiabilityType.RECURRING_OBLIGATION.value,
            principal_outstanding=premium or coverage,
            interest_rate=0.0,
            emi_amount=premium if premium else None,
            notes=notes,
        )
    )
    return out
