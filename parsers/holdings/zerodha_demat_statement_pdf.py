"""
Zerodha **Transaction With Holding Statement** PDF (monthly demat email).

Primary ingest is the **Statement of Account** block on page 1 — per-ISIN ledger lines with
``Buy/Cr`` and ``Sell/Dr`` quantities (``NSE Payout``, ``Delivery Out``, etc.). The trailing
**Holdings as on …** table is a month-end snapshot; we do **not** ingest it as holdings rows
(the ledger + FIFO derive, or tradebook CSV backup, drive positions).

Password: PAN via :class:`~api.models.PasswordTemplate` / ``UserSecrets``.
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path

import pdfplumber

from api.services.price_feed import canonical_nse_symbol
from pipeline.detection import DetectionResult, PARSER_LABELS
from pipeline.isin_nse_resolver import lookup_isin_symbol
from parsers.holdings.base import ParsedHolding, ParsedInvestmentTxn
from parsers.holdings.zerodha_tradebook import _ISIN_RE, aggregate_zerodha_trades
from pipeline.models import InvestmentTxnType

logger = logging.getLogger(__name__)

_ACCOUNT = "Zerodha"
_KIND = "zerodha_demat_statement_pdf"

_ISIN_HEADER = re.compile(
    r"^ISIN:\s*([A-Z0-9]{12})\s+Symbol:\s*(.+)$",
    re.IGNORECASE,
)
_TXN_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
_SKIP_LINE = re.compile(
    r"^(Opening balance|Closing balance|Date\s+Transaction|Statement of Account|Total:)\b",
    re.IGNORECASE,
)
_HOLDINGS_SECTION = re.compile(r"^Holdings as on\b", re.IGNORECASE)


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", (line or "").strip())


def _parse_txn_line(line: str) -> tuple[datetime.date, str, float, float, float] | None:
    """Parse ``YYYY-MM-DD <description> … <buy/cr> <sell/dr> <balance>``.

    Zerodha pads the middle with DP / settlement reference numbers; the last three tokens are
    always Buy/Cr qty, Sell/Dr qty, and running balance.
    """
    s = _norm_line(line)
    if not _TXN_DATE_PREFIX.match(s):
        return None
    parts = s.split()
    if len(parts) < 5:
        return None
    try:
        txn_date = datetime.date.fromisoformat(parts[0])
        buy_cr = float(parts[-3].replace(",", ""))
        sell_dr = float(parts[-2].replace(",", ""))
        _balance = float(parts[-1].replace(",", ""))
    except ValueError:
        return None
    middle = parts[1:-3]
    # Strip DP / settlement reference numbers (long digit tokens) after the human description.
    while middle and re.fullmatch(r"[\d.]+", middle[-1]):
        middle.pop()
    description = " ".join(middle).strip()
    if not description:
        return None
    return txn_date, description, buy_cr, sell_dr, _balance


def _resolve_symbol(isin: str, zerodha_symbol: str) -> str | None:
    nse = lookup_isin_symbol(isin)
    if nse:
        return canonical_nse_symbol(nse) or nse
    # Zerodha "Symbol:" on MF rows is a display label, not an NSE ticker.
    zs = (zerodha_symbol or "").strip()
    if zs and _ISIN_RE.match(isin) and isin.startswith("INE"):
        # Rare: equity ISIN but bhav map miss — do not treat MF-style label as symbol.
        return None
    return None


def parse_statement_of_account_text(text: str) -> list[ParsedInvestmentTxn]:
    """Parse the Statement of Account section from extracted PDF plain text."""
    current_isin: str | None = None
    current_symbol_label: str = ""
    out: list[ParsedInvestmentTxn] = []

    for raw in text.splitlines():
        line = _norm_line(raw)
        if not line:
            continue
        if _HOLDINGS_SECTION.match(line):
            break

        m_hdr = _ISIN_HEADER.match(line)
        if m_hdr:
            current_isin = m_hdr.group(1).upper()
            current_symbol_label = m_hdr.group(2).strip()
            continue

        if _SKIP_LINE.match(line):
            continue

        if current_isin is None:
            continue

        parsed = _parse_txn_line(line)
        if parsed is None:
            continue

        txn_date, description, buy_cr, sell_dr, _bal = parsed
        if buy_cr > 0 and sell_dr > 0:
            logger.debug("Zerodha demat: both Buy/Cr and Sell/Dr on one line — skip: %s", line)
            continue

        if buy_cr > 0:
            txn_type = InvestmentTxnType.BUY.value
            qty = buy_cr
        elif sell_dr > 0:
            txn_type = InvestmentTxnType.SELL.value
            qty = sell_dr
        else:
            continue

        sym = _resolve_symbol(current_isin, current_symbol_label)
        name = current_symbol_label or current_isin
        out.append(
            ParsedInvestmentTxn(
                txn_date=txn_date,
                symbol=sym,
                name=name,
                txn_type=txn_type,
                quantity=round(qty, 6),
                price_per_unit=0.0,
                total_amount=0.0,
                account_platform=_ACCOUNT,
                notes=description,
                metadata={
                    "kind": _KIND,
                    "isin": current_isin,
                    "zerodha_symbol": current_symbol_label,
                    "demat_description": description,
                    "source_layout": "zerodha_statement_of_account_text",
                },
            )
        )
    return out


def _extract_statement_text(pdf_path: Path) -> str:
    """Plain text until the holdings snapshot section (usually page 1)."""
    chunks: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            chunks.append(text)
            if _HOLDINGS_SECTION.search(text):
                break
    return "\n".join(chunks)


def parse_zerodha_demat_statement_pdf(
    pdf_path: str | Path,
    *,
    aggregate: bool = True,
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    """
    Parse a decrypted Zerodha monthly demat statement PDF.

    Returns:
        ``([], investment_txns)`` — holdings are derived downstream from the ledger, not from
        the month-end snapshot table in the PDF.
    """
    path = Path(pdf_path)
    text = _extract_statement_text(path)
    txns = parse_statement_of_account_text(text)
    if aggregate and txns:
        txns = aggregate_zerodha_trades(txns)
    if not txns:
        logger.info("Zerodha demat statement: 0 Statement-of-Account legs from %s", path.name)
    return [], txns


def detect_zerodha_demat_statement_pdf(path: str | Path) -> DetectionResult | None:
    """Sniff Zerodha Broking transaction-with-holding statement PDFs."""
    p = Path(path)
    if p.suffix.lower() != ".pdf" or not p.is_file():
        return None
    try:
        with pdfplumber.open(p) as pdf:
            blob = ""
            for page in pdf.pages[:3]:
                blob += (page.extract_text() or "") + "\n"
            low = blob.lower()
            if "zerodha" in low and "statement of account" in low and "buy/cr" in low:
                return DetectionResult(
                    source_type="zerodha_demat_statement_pdf",
                    confidence=0.9,
                    account_hint=None,
                    label=PARSER_LABELS["zerodha_demat_statement_pdf"],
                )
    except Exception:
        return None
    return None


__all__ = [
    "detect_zerodha_demat_statement_pdf",
    "parse_statement_of_account_text",
    "parse_zerodha_demat_statement_pdf",
]
