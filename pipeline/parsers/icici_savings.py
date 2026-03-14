"""
Parser for the ICICI savings account statement (PDF export).

Format: 7-column table across 4 pages.
  S No. | Transaction Date | Cheque Number | Transaction Remarks |
  Withdrawal Amount (INR) | Deposit Amount (INR) | Balance (INR)

Parsing strategy: pdfplumber word-level extraction with bounding boxes.

Why word-level instead of extract_text() or extract_tables()?
- extract_tables() only returns the column-header row (the table borders don't
  enclose the data rows in this PDF's structure).
- extract_text() gives correct text but can't tell us which "column" each piece
  of text belongs to when descriptions wrap across multiple lines.
- extract_words() gives us x/y coordinates for every word, so we can assign
  each word to the correct column by its x-position and correctly reconstruct
  multi-line transaction remarks.

Column x-ranges (measured from the actual PDF):
  S No.:       x0 < 60
  Date:        60  ≤ x0 < 80   (column header "Date" is at x≈74; data at x≈61)
  Cheque:      80  ≤ x0 < 192  (mostly empty — digital transactions)
  Remarks:     192 ≤ x0 < 400
  Withdrawal:  400 ≤ x0 < 465
  Deposit:     465 ≤ x0 < 535
  Balance:     x0 ≥ 535

Multi-line descriptions:
  Long remarks wrap both above and below the anchor line.  Between two
  consecutive anchor rows (rows that have S.No. + date), continuation lines
  (remarks-only rows) are split at the y-midpoint: those closer to the
  preceding anchor are its suffix; those closer to the following anchor are
  its prefix.  The concatenation order is: prefix + inline + suffix.
"""

from __future__ import annotations

import datetime
import re
import warnings
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber

from pipeline.models import ParsedTransaction
from pipeline.parsers.base import BaseParser

# ---------------------------------------------------------------------------
# Column x-coordinate boundaries (from empirical measurement)
# ---------------------------------------------------------------------------
_X_SNO_MAX = 60
_X_DATE_MIN = 60
_X_DATE_MAX = 80
_X_REMARKS_MIN = 192
_X_REMARKS_MAX = 400
_X_WITHDRAWAL_MIN = 400
_X_WITHDRAWAL_MAX = 465
_X_DEPOSIT_MIN = 465
_X_DEPOSIT_MAX = 520   # balance starts at ~x=530; deposits observed at x=483-495
_X_BALANCE_MIN = 520   # all observed balance values are x=530+ (header at x=532)

# Regex for the DD.MM.YYYY date pattern used in the data column
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

# y-bucketing tolerance: words within 4px of each other are on the same "line"
_Y_BUCKET_PX = 4

# Stop processing a page when we hit the closing note (last-page "Sincerly, Team ICICI Bank")
_STOP_WORDS = {"sincerly", "sincerely"}

# Every page has a privacy footer at the bottom of the table area.
# The footer starts with "www.icici.bank.in" followed by helpline/disclaimer text.
# We detect it by its most distinctive tokens and stop collecting continuations.
_FOOTER_MARKERS = {"www.icici.bank.in", "1800-1080"}


class ICICISavingsParser(BaseParser):

    @property
    def source_id(self) -> str:
        return "icici_savings"

    def parse(self, file_path: str | Path) -> list[ParsedTransaction]:
        """Open the ICICI savings PDF and return all transactions."""
        file_path = Path(file_path)
        rows: list[ParsedTransaction] = []

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    page_rows = self._parse_page(page, page_num)
                    rows.extend(page_rows)
                except Exception as exc:
                    warnings.warn(
                        f"[icici_savings] Error on page {page_num}: {exc}",
                        stacklevel=2,
                    )

        return rows

    # ------------------------------------------------------------------
    # Per-page parsing
    # ------------------------------------------------------------------

    def _parse_page(
        self, page: Any, page_num: int
    ) -> list[ParsedTransaction]:
        """Extract transactions from one page using word coordinates."""
        words = page.extract_words()

        # ── Group words into y-bucket "lines" ────────────────────────────
        y_groups: dict[int, list[dict]] = defaultdict(list)
        for word in words:
            bucket = round(word["top"] / _Y_BUCKET_PX) * _Y_BUCKET_PX
            y_groups[bucket].append(word)

        # ── Classify each line and collect into anchor / continuation ─────
        # An "anchor line" is a table data row that has an S.No. AND a date.
        # A "continuation line" has only remarks-column words (description wrap).
        anchors: list[dict] = []       # {"y": int, "sno": int, "date": date, "remarks": str,
                                        #  "withdrawal": Decimal, "deposit": Decimal, "balance": Decimal}
        continuations: list[dict] = [] # {"y": int, "text": str}
        stop_processing = False

        for y in sorted(y_groups.keys()):
            line_words = sorted(y_groups[y], key=lambda w: w["x0"])

            # Stop conditions — checked before we process the line:
            #   1. Closing "Sincerly, Team ICICI Bank" on the last page → Legends
            #   2. Page footer starting with "www.icici.bank.in 1800-1080" on every page
            for w in line_words:
                text_lower = w["text"].lower().rstrip(".,!")
                if text_lower in _STOP_WORDS or w["text"] in _FOOTER_MARKERS:
                    stop_processing = True
            if stop_processing:
                break

            classified = self._classify_line(line_words)
            if classified is None:
                continue

            kind, payload = classified
            if kind == "anchor":
                payload["y"] = y
                anchors.append(payload)
            elif kind == "continuation":
                continuations.append({"y": y, "text": payload})

        # ── Assign continuation lines to their anchor transactions ────────
        return self._build_transactions(anchors, continuations)

    # ------------------------------------------------------------------
    # Line classification
    # ------------------------------------------------------------------

    def _classify_line(
        self, line_words: list[dict]
    ) -> tuple[str, Any] | None:
        """Return ("anchor", data_dict), ("continuation", text), or None."""
        sno: int | None = None
        date_val: datetime.date | None = None
        remark_parts: list[tuple[float, str]] = []  # (x, text) for ordering
        withdrawal: Decimal | None = None
        deposit: Decimal | None = None
        balance: Decimal | None = None

        for w in line_words:
            x = w["x0"]
            text = w["text"].strip()
            if not text:
                continue

            # S.No. column — integer serial number
            if x < _X_SNO_MAX:
                if re.match(r"^\d+$", text):
                    sno = int(text)
                # non-numeric at S.No. x-range (footer text, legend letters) → skip whole line
                else:
                    return None

            # Date column
            elif _X_DATE_MIN <= x < _X_DATE_MAX:
                if _DATE_RE.match(text):
                    date_val = self._parse_date(text)
                else:
                    return None  # non-date in date column → skip

            # Remarks column
            elif _X_REMARKS_MIN <= x < _X_REMARKS_MAX:
                remark_parts.append((x, text))

            # Withdrawal column
            elif _X_WITHDRAWAL_MIN <= x < _X_WITHDRAWAL_MAX:
                withdrawal = self._parse_amount(text)

            # Deposit column
            elif _X_DEPOSIT_MIN <= x < _X_DEPOSIT_MAX:
                deposit = self._parse_amount(text)

            # Balance column
            elif x >= _X_BALANCE_MIN:
                balance = self._parse_amount(text)

            # Words in the "Cheque Number" range (80-192) or outside all ranges
            # are skipped — almost always empty or irrelevant.

        # Build the inline remarks string (words ordered by x-position)
        inline_remarks = " ".join(t for _, t in sorted(remark_parts))

        # ── Classify ─────────────────────────────────────────────────────
        if sno is not None and date_val is not None:
            # Anchor row: has S.No. + date (amounts may or may not be inline)
            return (
                "anchor",
                {
                    "sno": sno,
                    "date": date_val,
                    "inline_remarks": inline_remarks,
                    "withdrawal": withdrawal,
                    "deposit": deposit,
                    "balance": balance,
                },
            )

        if inline_remarks and sno is None and date_val is None:
            # Continuation row: only the remarks column has content
            return ("continuation", inline_remarks)

        # Everything else (page header, column headers, footer, legends): skip
        return None

    # ------------------------------------------------------------------
    # Build ParsedTransaction objects
    # ------------------------------------------------------------------

    def _build_transactions(
        self,
        anchors: list[dict],
        continuations: list[dict],
    ) -> list[ParsedTransaction]:
        """Stitch together anchor data + continuation remarks into transactions.

        Multi-line descriptions:
          Between two consecutive anchors at y=ya and y=yb, the midpoint is
          (ya + yb) / 2.  Continuation lines with y < midpoint are SUFFIXES of
          the first anchor; those with y > midpoint are PREFIXES of the second.
        """
        results: list[ParsedTransaction] = []

        for i, anchor in enumerate(anchors):
            ya = anchor["y"]
            prev_y = anchors[i - 1]["y"] if i > 0 else 0
            next_y = anchors[i + 1]["y"] if i + 1 < len(anchors) else float("inf")

            prev_mid = (prev_y + ya) / 2
            next_mid = (ya + next_y) / 2

            # Prefix: continuation lines between previous anchor and this one,
            # whose y is closer to this anchor than to the previous one.
            prefix_parts = [
                c["text"]
                for c in continuations
                if prev_mid < c["y"] < ya
            ]

            # Suffix: continuation lines between this anchor and the next one,
            # whose y is closer to this anchor than to the next one.
            suffix_parts = [
                c["text"]
                for c in continuations
                if ya < c["y"] < next_mid
            ]

            # Full description = prefix + inline + suffix, joined with space
            full_description = " ".join(
                filter(None, prefix_parts + [anchor["inline_remarks"]] + suffix_parts)
            ).strip()

            if not full_description:
                warnings.warn(
                    f"[icici_savings] Empty description for S.No. "
                    f"{anchor.get('sno')} — skipping.",
                    stacklevel=2,
                )
                continue

            # Determine debit vs credit from which amount column is populated
            withdrawal = anchor.get("withdrawal")
            deposit = anchor.get("deposit")

            # At least one must be present
            if withdrawal is None and deposit is None:
                warnings.warn(
                    f"[icici_savings] No amount for S.No. {anchor.get('sno')} "
                    f"({full_description[:40]}) — skipping.",
                    stacklevel=2,
                )
                continue

            debit_amount = withdrawal if withdrawal is not None else Decimal("0")
            credit_amount = deposit if deposit is not None else Decimal("0")

            # Validate: exactly one side non-zero (pydantic will catch it too)
            if debit_amount == 0 and credit_amount == 0:
                continue

            # Build metadata
            metadata: dict = {}
            if anchor.get("balance") is not None:
                pass  # balance stored in ParsedTransaction.closing_balance directly

            try:
                txn = ParsedTransaction(
                    txn_date=anchor["date"],
                    raw_description=full_description,
                    debit_amount=debit_amount,
                    credit_amount=credit_amount,
                    closing_balance=anchor.get("balance"),
                    metadata=metadata,
                )
                results.append(txn)
            except Exception as exc:
                warnings.warn(
                    f"[icici_savings] Could not build transaction for S.No. "
                    f"{anchor.get('sno')}: {exc}",
                    stacklevel=2,
                )

        return results

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(s: str) -> datetime.date | None:
        """Parse DD.MM.YYYY into a date. Returns None on failure."""
        try:
            return datetime.datetime.strptime(s.strip(), "%d.%m.%Y").date()
        except ValueError:
            return None

    @staticmethod
    def _parse_amount(s: str) -> Decimal | None:
        """Parse a decimal amount string. ICICI uses plain decimals (no Indian commas)."""
        s = s.strip().replace(",", "")
        if not s:
            return None
        try:
            val = Decimal(s)
            return val if val >= 0 else None
        except InvalidOperation:
            return None
