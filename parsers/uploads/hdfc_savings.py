"""
Parser for the HDFC savings account statement (.txt export).

Format quirks handled here (and ONLY here):
  - Optional blank or title lines before the CSV header — we locate the header by content.
  - Comma-delimited with heavy whitespace padding on every field.
  - Truncated header names (e.g. "Value Dat" instead of "Value Date").
  - Dates in DD/MM/YY format — parsed with explicit strptime, no guessing.
  - Both Debit and Credit amount columns always present; one is 0.00.
"""

from __future__ import annotations

import datetime
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pipeline.detection import DetectionResult, PARSER_LABELS
from pipeline.models import ParsedTransaction
from parsers.uploads.base import BaseParser

logger = logging.getLogger(__name__)


def _find_hdfc_savings_txt_header_index(raw_lines: list[str]) -> int | None:
    """Return the 0-based line index of the comma-separated CSV header row.

    HDFC sometimes ships a blank line or a title before the header; the header row
    contains ``Date`` plus ``Narration`` or ``Transaction`` (case-insensitive). We must
    **not** treat the first data row (which also has commas) as the header — that bug
    made :meth:`HDFCSavingsParser.detect` return ``None`` for normal exports.
    """
    for i, raw in enumerate(raw_lines):
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        if "date" in low and ("narration" in low or "transaction" in low):
            return i
    return None


class HDFCSavingsParser(BaseParser):

    @property
    def source_id(self) -> str:
        return "hdfc_savings"

    @classmethod
    def detect(cls, file_path: str | Path) -> DetectionResult | None:
        """HDFC savings exports are ``.txt`` with comma-separated 7-column rows."""
        path = Path(file_path)
        if path.is_dir():
            txts = list(path.glob("*.txt"))
            if not txts:
                return None
            path = sorted(txts)[0]
        elif path.suffix.lower() != ".txt":
            return None

        try:
            sample = path.read_text(encoding="utf-8", errors="replace")[:12000]
        except OSError:
            return None
        raw_lines = sample.splitlines()
        hi = _find_hdfc_savings_txt_header_index(raw_lines)
        if hi is None:
            return None
        # Need at least one plausible data row after the header.
        tail = [raw_lines[j].strip() for j in range(hi + 1, len(raw_lines)) if raw_lines[j].strip()]
        if not tail:
            return None
        hint: str | None = None
        nonempty = [ln.strip() for ln in raw_lines if ln.strip()]
        for ln in nonempty[:25]:
            m = re.search(r"(?:account|a/c)\s*(?:no\.?|number)\s*[:\s]*(\d{6,})", ln, re.I)
            if m:
                hint = m.group(1)[-4:]
                break
        return DetectionResult(
            source_type="hdfc_savings",
            confidence=0.92,
            account_hint=hint,
            label=PARSER_LABELS["hdfc_savings"],
        )

    def parse(self, file_path: str | Path) -> list[ParsedTransaction]:
        """Accept either a single .txt file or a directory of yearly .txt files.

        In directory mode, all matching files are parsed and results are merged
        and sorted chronologically before being returned — same pattern as the CC parser.
        """
        path = Path(file_path)

        if path.is_dir():
            rows: list[ParsedTransaction] = []
            # Sort so FY files are processed in chronological order.
            for txt_file in sorted(path.glob("*.txt")):
                rows.extend(self._parse_file(txt_file))
            return sorted(rows, key=lambda r: r.txn_date)

        return self._parse_file(path)

    # ------------------------------------------------------------------
    # Per-file parsing
    # ------------------------------------------------------------------

    def _parse_file(self, file_path: Path) -> list[ParsedTransaction]:
        """Parse a single HDFC savings .txt statement and return all transactions."""
        rows: list[ParsedTransaction] = []

        with open(file_path, encoding="utf-8") as fh:
            lines = fh.readlines()

        hi = _find_hdfc_savings_txt_header_index(lines)
        if hi is None:
            logger.warning("hdfc_savings: no CSV header row in %s", file_path.name)
            return []

        # All physical lines after the header row are candidates; skip blanks and the header
        # if it somehow repeats.
        for line_num, raw_line in enumerate(lines[hi + 1 :], start=hi + 2):
            line = raw_line.strip()
            if not line:
                continue
            low = line.lower()
            if "date" in low and ("narration" in low or "transaction" in low):
                continue

            parsed = self._parse_line(line, line_num, file_path.name)
            if parsed is not None:
                rows.append(parsed)

        return rows

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_line(
        self, line: str, line_num: int, filename: str
    ) -> ParsedTransaction | None:
        """Parse a single comma-delimited, whitespace-padded line."""
        parts = [p.strip() for p in line.split(",")]

        # The HDFC format has 7 columns. Narration can contain commas
        # (rare but possible), so we split into at most 7 from the right
        # to keep narration intact if it has internal commas.
        # Format: Date, Narration, Value Dat, Debit, Credit, Ref, Balance
        # However the narration field is padded wide enough that commas
        # inside it are extremely rare. If we get more than 7 parts,
        # the extra ones belong to the narration.
        if len(parts) < 7:
            return None

        # Rejoin overflow parts into narration (parts 1 through -5)
        date_str = parts[0]
        narration = ",".join(parts[1:-5]).strip()
        value_date_str = parts[-5]
        debit_str = parts[-4]
        credit_str = parts[-3]
        ref_str = parts[-2]
        balance_str = parts[-1]

        txn_date = self._parse_date(date_str, line_num)
        if txn_date is None:
            return None

        value_date = self._parse_date(value_date_str, line_num)
        debit = self._parse_amount(debit_str)
        credit = self._parse_amount(credit_str)
        balance = self._parse_amount(balance_str)

        return ParsedTransaction(
            txn_date=txn_date,
            raw_description=narration,
            debit_amount=debit,
            credit_amount=credit,
            ref_number=ref_str if ref_str else None,
            closing_balance=balance if balance > 0 else None,
            value_date=value_date,
        )

    @staticmethod
    def _parse_date(s: str, line_num: int) -> datetime.date | None:
        """Parse DD/MM/YY into a date. Returns None on failure."""
        s = s.strip()
        if not s:
            return None
        for fmt in ("%d/%m/%y", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        logger.warning("hdfc_savings: could not parse date %r on line %d", s, line_num)
        return None

    @staticmethod
    def _parse_amount(s: str) -> Decimal:
        """Parse a whitespace-padded amount string like '  9000.00  '."""
        s = s.strip()
        if not s:
            return Decimal("0")
        try:
            return Decimal(s)
        except InvalidOperation:
            return Decimal("0")
