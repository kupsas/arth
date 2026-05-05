"""
HDFC credit card **statement PDF** parser (email attachment).

HDFC emails password-protected CC PDFs whose layout differs from the tilde-delimited
CSV export. There are **two** PDF generations:

**New (≈2026+)** — pipe-separated time column::

    15/02/2026| 10:04 EMI GPAY UTILITIESMUMBAI C 2,640.30 l
    01/03/2026| 13:47 CREDIT CARD PAYMENTNet Banking (Ref# ...) + C 91,275.00 l

**Legacy (e.g. 2025)** — plain date, amount at end, credits suffixed ``Cr``::

    16/04/2025 5% Swiggy Cashback (Ref# ...) 944.08Cr
    18/04/2025 SWIGGY BANGALORE 760.00

**Direction rule (new PDF):** the bank prints ``C`` before the INR amount.
Purchases use ``C 2,640.30``; credits (payments, cashback) use ``+ C 500.00`` — the
literal ``+`` before ``C`` marks an inflow (same semantics as the CSV's ``Cr`` column).

Output matches :class:`~pipeline.parsers.hdfc_cc.HDFCCreditCardParser` (same
``ParsedTransaction`` shape and ``metadata`` keys) so downstream transform/rules/LLM
see identical rows whether the source was CSV or PDF.

Sections **Domestic** vs **International** are taken from HDFC's PDF headings; we set
``metadata["domestic_or_international"]`` to ``"domestic"`` / ``"international"``
(plus ``"channel_hint": "CARD"``).
"""

from __future__ import annotations

import logging
import re
import warnings
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pdfplumber

from pipeline.detection import DetectionResult, PARSER_LABELS
from pipeline.models import ParsedTransaction
from pipeline.parsers.base import BaseParser
from pipeline.parsers.hdfc_cc import HDFCCreditCardParser

logger = logging.getLogger(__name__)

# After normalising the first ``|``, lines start with a date and optional time.
_DATE_LINE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s*\|\s*(.+)$",
)

# Every INR amount in the txn line uses this pattern; the **last** match is the
# charge (foreign txns also show ``USD xx.xx`` earlier in the line).
# Debits: ``C 2,640.30`` — credits: ``+ C 500.00`` (optional ``+`` before ``C``).
_INR_AMOUNT = re.compile(r"(\+)?\s*C\s*([\d,]+\.\d{2})")

# Legacy: ``16/04/2025 DESCRIPTION... 760.00`` or ``... 944.08Cr`` (no ``|``).
_LEGACY_DATE_LINE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+)$")

# PDF section headings — must match :func:`_parse_unified_line` ``section`` parameter.
_CC_SECTION = Literal["domestic", "international"]


_CC_NUM_HINT = re.compile(
    r"(?:Credit\s*Card|CARD)\s*(?:No\.?|Number)?\s*[^\d\n]*(\d{4})(?:\D|$)",
    re.IGNORECASE | re.DOTALL,
)


class HDFCCreditCardPdfParser(BaseParser):
    """Parse one monthly HDFC CC statement PDF into :class:`ParsedTransaction` rows."""

    @property
    def source_id(self) -> str:
        return "hdfc_cc_pdf"

    @classmethod
    def detect(cls, file_path: str | Path) -> DetectionResult | None:
        """HDFC credit card statement PDF: *Credit Card* in header + txn date lines."""
        path = Path(file_path)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            return None
        try:
            with pdfplumber.open(path) as pdf:
                if not pdf.pages:
                    return None
                chunks: list[str] = []
                for i in range(min(3, len(pdf.pages))):
                    chunks.append(pdf.pages[i].extract_text() or "")
                text = "\n".join(chunks)
        except Exception:
            return None
        tl = text.lower()
        if "credit card" not in tl and not ("hdfc" in tl and "card" in tl):
            return None
        if "hdfc" not in tl and "hdfc" not in path.name.lower():
            return None
        hint: str | None = None
        m = _CC_NUM_HINT.search(text)
        if m:
            hint = m.group(1)
        has_pipe_txn = bool(re.search(r"\d{2}/\d{2}/\d{4}\s*\|", text))
        flat = re.sub(r"[\r\n]+", " ", text[:12000])
        has_legacy = bool(re.search(r"\d{2}/\d{2}/\d{4}\s+[A-Za-z]", flat))
        if not has_pipe_txn and not has_legacy:
            if "domestic" not in tl and "international" not in tl and "outstanding" not in tl:
                return None
        return DetectionResult(
            source_type="hdfc_cc_pdf",
            confidence=0.9,
            account_hint=hint,
            label=PARSER_LABELS["hdfc_cc_pdf"],
        )

    def parse(self, file_path: str | Path) -> list[ParsedTransaction]:
        """Read *file_path* (decrypted PDF) and return all card transactions."""
        path = Path(file_path)
        if not path.is_file():
            warnings.warn(f"[hdfc_cc_pdf] Not a file: {path}", stacklevel=2)
            return []

        lines_with_section = _extract_txn_lines(path)
        if not lines_with_section:
            lines_with_section = _extract_legacy_txn_lines(path)

        rows: list[ParsedTransaction] = []
        csv_helpers = HDFCCreditCardParser()

        for section, desc_prefix, raw_line in lines_with_section:
            pt = _parse_unified_line(
                raw_line, section, csv_helpers, desc_prefix=desc_prefix
            )
            if pt is not None:
                rows.append(pt)

        return rows


def _normalize_leading_pipe(line: str) -> str:
    """Make ``DD/MM/YYYY | HH:MM`` and ``DD/MM/YYYY| HH:MM`` the same shape."""
    line = line.strip()
    m = re.match(r"^(\d{2}/\d{2}/\d{4})\s*\|\s*(.*)$", line)
    if m:
        return f"{m.group(1)}| {m.group(2)}"
    return line


def _ref_continuation_line(t: str) -> bool:
    """HDFC sometimes puts the tail of a long ``(Ref# …)`` on the line *after* the date row."""
    if not t:
        return False
    if re.match(r"^[0-9]{12,}\)?$", t):
        return True
    return bool(re.match(r"^[0-9]{6,}\)[A-Z]{0,3}$", t))


def _is_noise_line(t: str) -> bool:
    """Lines that are never part of a txn description (footers, GST blocks, etc.)."""
    tl = t.lower()
    if "gst" in tl and "invoice" in tl:
        return True
    if t.startswith("Page ") and " of " in t:
        return True
    if t.startswith("Useful Links") or t.startswith("Statement & Payment"):
        return True
    return False


def _is_noise_line_legacy(t: str) -> bool:
    """Extra skips for older statement layouts."""
    tl = t.lower()
    if tl.startswith("cashback summary") or tl.startswith("opening balance"):
        return True
    if "important information" in tl and len(t) < 80:
        return True
    return False


def _extract_legacy_txn_lines(pdf_path: Path) -> list[tuple[_CC_SECTION, str, str]]:
    """Older PDFs: ``DD/MM/YYYY MERCHANT … 1,234.56`` or ``… 99.00Cr`` (no pipe column)."""
    section: _CC_SECTION = "domestic"
    out: list[tuple[_CC_SECTION, str, str]] = []
    prefix_lines: list[str] = []
    seen_column_header = False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            prefix_lines = []
            seen_column_header = False
            text = page.extract_text() or ""
            lines = text.splitlines()
            idx = 0
            while idx < len(lines):
                line = lines[idx]
                t = line.strip()
                if not t:
                    idx += 1
                    continue
                if t.lower().startswith("null "):
                    t = t[5:].strip()

                if t == "Domestic Transactions" or (
                    t.startswith("Domestic Transactions") and "Date" not in t
                ):
                    section = "domestic"
                    prefix_lines = []
                    seen_column_header = False
                    idx += 1
                    continue
                if t.startswith("International Transactions"):
                    section = "international"
                    prefix_lines = []
                    seen_column_header = False
                    idx += 1
                    continue

                # ``Date Transaction Description Amount (in Rs.)`` (may wrap / truncate).
                if "Date" in t and "Transaction" in t:
                    prefix_lines = []
                    seen_column_header = True
                    idx += 1
                    continue

                if seen_column_header and _is_noise_line_legacy(t):
                    idx += 1
                    continue

                if not t[0].isdigit():
                    if seen_column_header and not _is_noise_line(t):
                        prefix_lines.append(t)
                    idx += 1
                    continue

                # New-format row — let the primary extractor handle it if mixed PDF.
                if re.match(r"^\d{2}/\d{2}/\d{4}\s*\|", t):
                    idx += 1
                    continue

                m = _LEGACY_DATE_LINE.match(t)
                if not m:
                    idx += 1
                    continue

                desc_prefix = " ".join(prefix_lines)
                prefix_lines = []
                out.append((section, desc_prefix, t))
                idx += 1

    return out


def _parse_unified_line(
    raw_line: str,
    section: Literal["domestic", "international"],
    csv_helpers: HDFCCreditCardParser,
    *,
    desc_prefix: str = "",
) -> ParsedTransaction | None:
    """Dispatch to new (pipe) or legacy (Cr / plain amount) row parser."""
    s = raw_line.strip()
    norm = _normalize_leading_pipe(s)
    if _DATE_LINE.match(norm):
        return _parse_one_line(norm, section, csv_helpers, desc_prefix=desc_prefix)
    return _parse_legacy_line(s, section, csv_helpers, desc_prefix=desc_prefix)


def _split_legacy_rest(
    rest: str, csv_helpers: HDFCCreditCardParser
) -> tuple[str, Decimal, bool] | None:
    """Last ``…,###.##`` on the line is INR; optional trailing ``Cr`` = credit."""
    rest = rest.strip()
    is_credit = bool(re.search(r"Cr\s*$", rest, re.I))
    if is_credit:
        rest = re.sub(r"\s*Cr\s*$", "", rest, flags=re.I).strip()

    m = re.search(r"([\d,]+\.\d{2})\s*$", rest)
    if not m:
        return None
    amount = csv_helpers._parse_amount(m.group(1))  # noqa: SLF001
    if amount is None or amount == Decimal("0"):
        return None
    desc = rest[: m.start()].strip()
    return desc, amount, is_credit


def _parse_legacy_line(
    line: str,
    section: Literal["domestic", "international"],
    csv_helpers: HDFCCreditCardParser,
    *,
    desc_prefix: str = "",
) -> ParsedTransaction | None:
    """Parse legacy ``DD/MM/YYYY … amount`` / ``… amountCr`` row."""
    line = re.sub(r"^null\s+", "", line.strip(), flags=re.I)
    if "|" in line[:14]:
        return None

    m = _LEGACY_DATE_LINE.match(line)
    if not m:
        return None

    date_str = m.group(1)
    rest = m.group(2).strip()

    split = _split_legacy_rest(rest, csv_helpers)
    if split is None:
        return None
    body, amount, is_credit = split

    if desc_prefix:
        body = f"{desc_prefix} {body}".strip()

    txn_date = csv_helpers._parse_date(date_str, "hdfc_cc_pdf.pdf")  # noqa: SLF001
    if txn_date is None:
        return None

    if is_credit:
        debit_amount = Decimal("0")
        credit_amount = amount
    else:
        debit_amount = amount
        credit_amount = Decimal("0")

    dom_int = "international" if section == "international" else "domestic"
    metadata: dict = {
        "domestic_or_international": dom_int,
        "channel_hint": "CARD",
    }

    return ParsedTransaction(
        txn_date=txn_date,
        raw_description=" ".join(body.split()),
        debit_amount=debit_amount,
        credit_amount=credit_amount,
        metadata=metadata,
    )


def _extract_txn_lines(pdf_path: Path) -> list[tuple[_CC_SECTION, str, str]]:
    """Walk the PDF in page order, track Domestic vs International.

    Returns:
        List of ``(section, description_prefix, date_line)``.  HDFC sometimes splits
        a single txn across lines (e.g. merchant text above, ``DD/MM/YYYY|…`` below);
        *description_prefix* holds the text **immediately above** the date line so we
        do not lose ``CREDIT CARD PAYMENT…`` style narrations.

    We use :meth:`pdfplumber.Page.extract_text` (not ``extract_tables``) because HDFC
    sometimes merges cells; line-based regex is more stable than fragile table grids.
    """
    section: _CC_SECTION = "domestic"
    out: list[tuple[_CC_SECTION, str, str]] = []
    prefix_lines: list[str] = []
    # Ignore the address / summary block at the top of each page until we see the
    # real column header — otherwise the first txn would inherit your mailing address.
    seen_column_header = False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            prefix_lines = []
            seen_column_header = False
            text = page.extract_text() or ""
            lines = text.splitlines()
            idx = 0
            while idx < len(lines):
                line = lines[idx]
                t = line.strip()
                if not t:
                    idx += 1
                    continue

                # Section headers (standalone or followed by column titles on same line).
                if t == "Domestic Transactions" or (
                    t.startswith("Domestic Transactions") and "DATE &" not in t
                ):
                    section = "domestic"
                    prefix_lines = []
                    seen_column_header = False
                    idx += 1
                    continue
                if t.startswith("International Transactions"):
                    section = "international"
                    prefix_lines = []
                    seen_column_header = False
                    idx += 1
                    continue

                # Column header row — real txn table begins after this.
                if "DATE & TIME" in t and "TRANSACTION" in t:
                    prefix_lines = []
                    seen_column_header = True
                    idx += 1
                    continue

                norm = _normalize_leading_pipe(t)
                if _DATE_LINE.match(norm):
                    desc_prefix = " ".join(prefix_lines)
                    prefix_lines = []
                    # Long ``Ref#`` values occasionally wrap — grab the numeric tail on the next line.
                    if idx + 1 < len(lines):
                        nxt = lines[idx + 1].strip()
                        if _ref_continuation_line(nxt):
                            desc_prefix = f"{desc_prefix} {nxt}".strip()
                            idx += 1
                    out.append((section, desc_prefix, norm))
                    idx += 1
                    continue

                if seen_column_header and not _is_noise_line(t):
                    prefix_lines.append(t)

                idx += 1

    return out


def _parse_one_line(
    line: str,
    section: Literal["domestic", "international"],
    csv_helpers: HDFCCreditCardParser,
    *,
    desc_prefix: str = "",
) -> ParsedTransaction | None:
    """Turn one statement text line into a :class:`ParsedTransaction` or skip."""

    m = _DATE_LINE.match(line.strip())
    if not m:
        return None

    date_str = m.group(1)
    rest = m.group(2).strip()

    # All INR amounts on the line; the **last** one is this txn's settlement amount.
    matches = list(_INR_AMOUNT.finditer(rest))
    if not matches:
        logger.debug("hdfc_cc_pdf: no INR amount in line: %s", line[:120])
        return None

    last = matches[-1]
    is_credit = last.group(1) == "+"
    amt_raw = last.group(2)
    amount = csv_helpers._parse_amount(amt_raw)  # noqa: SLF001 — reuse CSV Indian-format parser
    if amount is None or amount == Decimal("0"):
        return None

    # Description = text before the last ``[+] C <amount>`` token.
    desc_end = last.start()
    body = rest[:desc_end].strip()

    # Strip leading HH:MM if present (time is not stored separately today).
    # Handle both ``10:04 EMI …`` and a bare ``13:47`` when ``+ C`` sits flush after time.
    body = re.sub(r"^\d{2}:\d{2}(\s+|$)", "", body).strip()

    # Multi-line PDF rows: merchant / payment text was collected *above* the date line.
    if desc_prefix:
        body = f"{desc_prefix} {body}".strip()

    txn_date = csv_helpers._parse_date(date_str, "hdfc_cc_pdf.pdf")  # noqa: SLF001
    if txn_date is None:
        return None

    if is_credit:
        debit_amount = Decimal("0")
        credit_amount = amount
    else:
        debit_amount = amount
        credit_amount = Decimal("0")

    dom_int = "international" if section == "international" else "domestic"
    metadata: dict = {
        "domestic_or_international": dom_int,
        "channel_hint": "CARD",
    }

    description = " ".join(body.split())

    return ParsedTransaction(
        txn_date=txn_date,
        raw_description=description,
        debit_amount=debit_amount,
        credit_amount=credit_amount,
        metadata=metadata,
    )
