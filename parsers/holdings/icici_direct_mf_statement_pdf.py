"""
ICICI Securities **Mutual Fund Account Statement** PDF (distributor statement).

Transaction lines are easier to recover from **plain text** than from ``extract_tables()``
(brochure-style headers confuse table detection). We scan ``extract_text()`` line-by-line,
track the current folio and scheme name, and parse dated ledger rows.

Callers can run :func:`parsers.holdings.icici_direct_mf.derive_mf_holdings` on the
returned transactions to rebuild ``ParsedHolding`` rows (mirrors CSV ingest).
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path

import pdfplumber

from pipeline.detection import DetectionResult, PARSER_LABELS
from parsers.holdings.base import ParsedInvestmentTxn, parse_icici_number
from pipeline.models import InvestmentTxnType

logger = logging.getLogger(__name__)

_ACCOUNT = "ICICI Direct MF"
_KIND = "icici_mf_account_statement_pdf"

_FOLIO_LINE = re.compile(
    r"^(?P<amc>.*?)Folio\s+No\s*:\s*(?P<fol>\d+)",
    re.IGNORECASE,
)


def _parse_stmt_date(s: str) -> datetime.date | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _txn_type_from_mf_label(raw: str) -> str | None:
    s = raw.strip().lower()
    if "sip" in s or s == "purchase-sip":
        return InvestmentTxnType.SIP.value
    if s.startswith("purchase") or s.startswith("buy"):
        return InvestmentTxnType.BUY.value
    # "redemption" does not contain the substring "redeem"
    if "redeem" in s or s.startswith("redemption"):
        return InvestmentTxnType.SELL.value
    if "switch" in s:
        if "in" in s:
            return InvestmentTxnType.SWITCH_IN.value
        if "out" in s:
            return InvestmentTxnType.SWITCH_OUT.value
    return None


def _is_numeric_token(tok: str) -> bool:
    t = tok.replace(",", "").strip()
    if not t or t in (".", "-"):
        return False
    try:
        float(t)
    except ValueError:
        return False
    return True


def _parse_mf_ledger_line(line: str) -> tuple[datetime.date, str, str, list[str]] | None:
    """Parse ``09-May-2025 196713330 Purchase-SIP …`` → date, txn id, type, six numeric cells."""
    parts = line.split()
    if len(parts) < 3 + 6:
        return None
    d = _parse_stmt_date(parts[0])
    if d is None:
        return None
    txn_no = parts[1]
    tail = parts[-6:]
    if not all(_is_numeric_token(x) for x in tail):
        return None
    type_toks = parts[2:-6]
    if not type_toks:
        return None
    return d, txn_no, " ".join(type_toks), tail


def parse_icici_direct_mf_statement_pdf(pdf_path: str | Path) -> list[ParsedInvestmentTxn]:
    """Parse MF account statement PDF into ``ParsedInvestmentTxn`` rows."""
    path = Path(pdf_path)
    text = _extract_all_text(path)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    current_folio = ""
    fund_name = ""
    scheme_name = ""
    expect_scheme = False
    out: list[ParsedInvestmentTxn] = []

    for ln in lines:
        fm = _FOLIO_LINE.search(ln)
        if fm:
            current_folio = fm.group("fol").strip()
            fund_name = re.sub(r"\s+", " ", (fm.group("amc") or "").strip())
            scheme_name = ""
            expect_scheme = True
            continue

        low = ln.lower()
        if "opening balance" in low or "current unit balance" in low:
            continue
        if low.startswith("page ") or "account summary" in low:
            continue
        if "holding pattern" in low or low.startswith("instructions"):
            break

        parsed: tuple[datetime.date, str, str, list[str]] | None = None

        if expect_scheme:
            pl = _parse_mf_ledger_line(ln)
            if pl:
                expect_scheme = False
                parsed = pl
            elif "opening balance" in low:
                expect_scheme = False
                continue
            else:
                scheme_name = ln.strip()
                expect_scheme = False
                continue
        else:
            parsed = _parse_mf_ledger_line(ln)

        if not parsed:
            continue

        txn_date, _tno, type_str, num_cells = parsed
        txn_kind = _txn_type_from_mf_label(type_str)
        if txn_kind is None:
            logger.debug("MF stmt: skip unknown txn type %r", type_str)
            continue

        price = parse_icici_number(num_cells[0])
        units = parse_icici_number(num_cells[1])
        gross = parse_icici_number(num_cells[2])
        _tds = parse_icici_number(num_cells[3])
        _stt = parse_icici_number(num_cells[4])
        net = parse_icici_number(num_cells[5])

        qty = abs(units)
        total = abs(net if net else gross)
        ppu = abs(price) if price else (total / qty if qty else 0.0)

        display = f"{fund_name} — {scheme_name}".strip(" —") if fund_name or scheme_name else "MF"

        out.append(
            ParsedInvestmentTxn(
                txn_date=txn_date,
                symbol=None,
                name=display,
                txn_type=txn_kind,
                quantity=qty,
                price_per_unit=round(ppu, 6),
                total_amount=round(total, 2),
                account_platform=_ACCOUNT,
                notes=f"Folio {current_folio}" if current_folio else None,
                metadata={
                    "kind": _KIND,
                    "folio": current_folio,
                    "fund_name": fund_name,
                    "scheme_name": scheme_name,
                    "txn_label": type_str,
                    "source_layout": "icici_mf_account_statement_text",
                },
            )
        )

    if not out:
        logger.info("ICICI MF account statement: 0 txns from %s", path.name)
    return out


def _extract_all_text(pdf_path: Path) -> str:
    parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
    return "\n".join(parts)


def detect_icici_mf_statement_pdf(path: str | Path) -> DetectionResult | None:
    """Mutual fund account statement PDF: Folio No band + ledger rows."""
    p = Path(path)
    if p.suffix.lower() != ".pdf" or not p.is_file():
        return None
    try:
        text = _extract_all_text(p)[:25_000]
    except Exception:
        return None
    tl = text.lower()
    if not _FOLIO_LINE.search(text):
        if "folio" not in tl or "mutual fund" not in tl:
            return None
    if "account statement" in tl or "folio" in tl or "scheme" in tl:
        return DetectionResult(
            source_type="icici_direct_mf_statement_pdf",
            confidence=0.84,
            account_hint=None,
            label=PARSER_LABELS["icici_direct_mf_statement_pdf"],
        )
    return None
