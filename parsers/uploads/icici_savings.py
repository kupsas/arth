"""
Parser for the ICICI savings account statement (PDF export).

Two layouts exist:

**Legacy (manual export / older PDFs)** — 7-column table across several pages:
  S No. | Transaction Date (DD.MM.YYYY) | Cheque | Remarks |
  Withdrawal | Deposit | Balance

**Combined email statement (current)** — grid:
  DATE (DD-MM-YYYY) | MODE | PARTICULARS | DEPOSITS | WITHDRAWALS | BALANCE
  Same word-level strategy; different x-bands and date format.
  Some PDFs stack **PPF** then **Savings** on the same page; each section ends with a
  ``Total:`` summary row — those rows must be skipped (not treated as end-of-page).

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

from pipeline.detection import DetectionResult, PARSER_LABELS
from pipeline.models import ParsedTransaction
from parsers.uploads.base import BaseParser

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

# Regex for the DD.MM.YYYY date pattern used in the legacy data column
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

# Combined-statement layout (emailed PDFs): DD-MM-YYYY, often at x0≈35
_COMBINED_DATE_PREFIX_MAX = 58
_COMBINED_PARTICULARS_MIN = 95
_COMBINED_PARTICULARS_MAX = 370
# Amount columns drift slightly between statement years — use wide bands.
_COMBINED_DEPOSIT_X = (340, 432)
_COMBINED_WITHDRAWAL_X = (432, 515)
# Balance column — we parse for sanity but do not emit as txn amount.
_COMBINED_BALANCE_X_MIN = 515


def _line_rows_from_page(page: Any) -> list[tuple[float, str, list[dict]]]:
    """Sorted (y-bucket, joined text, line_words) for section detection."""
    words = page.extract_words()
    y_groups: dict[int, list[dict]] = defaultdict(list)
    for word in words:
        bucket = round(word["top"] / _Y_BUCKET_PX) * _Y_BUCKET_PX
        y_groups[bucket].append(word)
    out: list[tuple[float, str, list[dict]]] = []
    for y in sorted(y_groups.keys()):
        line_words = sorted(y_groups[y], key=lambda w: w["x0"])
        txt = " ".join(w["text"] for w in line_words)
        out.append((float(y), txt, line_words))
    return out


def _annual_pdf_has_ppf_block(pdf: Any) -> bool:
    """Annual multi-account PDF: summary lists PPF and body has two statement sections."""
    if len(pdf.pages) < 1:
        return False
    t = pdf.pages[0].extract_text() or ""
    return (
        "PPF A/c" in t
        and "Statement of Transactions in Savings Account" in t
        and "Statement of Transactions in Account Number:" in t
    )


def _needs_savings_only_band(pdf: Any) -> bool:
    """True when page 1 stacks another combined-layout table above the savings section.

    ICICI **monthly** email PDFs use ``Statement of Transactions in PPF Account``;
    **annual** PDFs use ``Statement of Transactions in Account Number:`` (PPF) plus
    ``PPF A/c`` summary. In both cases :meth:`ICICISavingsParser` must only parse
    rows at/after ``Statement of Transactions in Savings Account`` so PPF rows are
    not stamped as savings — PPF is handled by :func:`parse_icici_ppf_from_combined_pdf`.
    """
    if len(pdf.pages) < 1:
        return False
    t = pdf.pages[0].extract_text() or ""
    if "Statement of Transactions in Savings Account" not in t:
        return False
    if "Statement of Transactions in PPF Account" in t:
        return True
    return _annual_pdf_has_ppf_block(pdf)


def _combined_savings_y_window(
    pdf: Any,
    page: Any,
    page_num: int,
    savings_only_band: bool,
) -> tuple[float | None, float | None]:
    """When ``savings_only_band``, page-1 savings rows start at the Savings statement header."""
    if not savings_only_band or page_num != 1:
        return (None, None)
    for y, txt, _ in _line_rows_from_page(page):
        if "Statement of Transactions in Savings Account" in txt:
            return (y, None)
    return (None, None)


def combined_ppf_y_window_page1(page: Any) -> tuple[float, float] | None:
    """Vertical span ``[y_lo, y_hi)`` of the PPF table on combined statement page 1.

    ``y_hi`` is the Savings section header line — same band :func:`parse_icici_ppf_from_combined_pdf`
    passes to :meth:`ICICISavingsParser._parse_page_combined`.

    Supports:

    - **Annual:** PPF block title ``Statement of Transactions in Account Number:`` (non-savings).
    - **Monthly email:** ``Statement of Transactions in PPF Account``.
    """
    y_ppf: float | None = None
    y_sav: float | None = None
    for y, txt, _ in _line_rows_from_page(page):
        if "Statement of Transactions in PPF Account" in txt:
            if y_ppf is None:
                y_ppf = y
        elif (
            "Statement of Transactions in Account Number:" in txt
            and "Savings" not in txt
        ):
            if y_ppf is None:
                y_ppf = y
        if "Statement of Transactions in Savings Account" in txt:
            y_sav = y
            break
    if y_ppf is None or y_sav is None:
        return None
    return (y_ppf, y_sav)

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

    @classmethod
    def detect(cls, file_path: str | Path) -> DetectionResult | None:
        """ICICI Bank savings statement PDF (legacy grid or combined email layout)."""
        path = Path(file_path)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            return None
        try:
            with pdfplumber.open(path) as pdf:
                if not pdf.pages:
                    return None
                chunk = ""
                for i in range(min(3, len(pdf.pages))):
                    chunk += pdf.pages[i].extract_text() or ""
        except Exception:
            return None
        tl = chunk.lower()
        if "icici" not in tl:
            return None
        # Avoid confusing ICICI MF account statement PDFs with bank savings.
        if (
            "folio no" in tl
            and "mutual fund" in tl
            and "statement of transactions in savings account" not in tl
        ):
            return None

        savings_markers = (
            "statement of transactions in savings account" in tl,
            "statement of transactions in ppf account" in tl
            and "statement of transactions in savings account" in tl,
            "transaction date" in tl and "remarks" in tl and "withdrawal" in tl,
            "particulars" in tl and "withdrawals" in tl and "deposits" in tl,
            "savings account" in tl and "withdrawal" in tl and "deposit" in tl,
        )
        if not any(savings_markers):
            return None

        return DetectionResult(
            source_type="icici_savings",
            confidence=0.88,
            account_hint=None,
            label=PARSER_LABELS["icici_savings"],
        )

    def parse(self, file_path: str | Path) -> list[ParsedTransaction]:
        """Accept either a single .pdf file or a directory of yearly .pdf files.

        In directory mode, all matching files are parsed and results are merged
        and sorted chronologically before being returned — same pattern as the CC parser.
        """
        path = Path(file_path)

        if path.is_dir():
            rows: list[ParsedTransaction] = []
            # Sort so yearly PDFs are processed in chronological order.
            for pdf_file in sorted(path.glob("*.pdf")):
                rows.extend(self._parse_file(pdf_file))
            return sorted(rows, key=lambda r: r.txn_date)

        return self._parse_file(path)

    # ------------------------------------------------------------------
    # Per-file parsing
    # ------------------------------------------------------------------

    def _parse_file(self, file_path: Path) -> list[ParsedTransaction]:
        """Open a single ICICI savings PDF and return all its transactions."""
        rows: list[ParsedTransaction] = []

        with pdfplumber.open(file_path) as pdf:
            use_combined = self._pdf_uses_combined_statement_layout(pdf)
            savings_only_band = _needs_savings_only_band(pdf)
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    if use_combined:
                        y_min, y_max = _combined_savings_y_window(
                            pdf, page, page_num, savings_only_band
                        )
                        page_rows = self._parse_page_combined(
                            page, page_num, y_min=y_min, y_max=y_max
                        )
                    else:
                        page_rows = self._parse_page(page, page_num)
                    rows.extend(page_rows)
                except Exception as exc:
                    warnings.warn(
                        f"[icici_savings] Error on page {page_num} of "
                        f"{file_path.name}: {exc}",
                        stacklevel=2,
                    )

        return rows

    def _pdf_uses_combined_statement_layout(self, pdf: Any) -> bool:
        """Emailed ICICI PDFs use DD-MM-YYYY + PARTICULARS; legacy uses S.No. + DD.MM.YYYY."""
        if not pdf.pages:
            return False
        text = pdf.pages[0].extract_text() or ""
        return "Statement of Transactions" in text and "PARTICULARS" in text

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
    # Combined email-statement layout (DATE | PARTICULARS | DEPOSITS | …)
    # ------------------------------------------------------------------

    def _parse_page_combined(
        self,
        page: Any,
        page_num: int,
        *,
        y_min: float | None = None,
        y_max: float | None = None,
    ) -> list[ParsedTransaction]:
        """Parse one page of the DD-MM-YYYY emailed combined statement.

        ``y_min`` / ``y_max`` restrict which horizontal bands to include (annual PDF:
        savings-only or PPF-only band on page 1).
        """
        words = page.extract_words()
        y_groups: dict[int, list[dict]] = defaultdict(list)
        for word in words:
            bucket = round(word["top"] / _Y_BUCKET_PX) * _Y_BUCKET_PX
            y_groups[bucket].append(word)

        anchors: list[dict] = []
        continuations: list[dict] = []

        for y in sorted(y_groups.keys()):
            if y_min is not None and y < y_min:
                continue
            if y_max is not None and y >= y_max:
                continue
            line_words = sorted(y_groups[y], key=lambda w: w["x0"])
            joined = " ".join(w["text"] for w in line_words)
            jl = joined.lower()
            if "legends" in jl and "statement" in jl:
                break
            if "reward points summary" in jl:
                break

            classified = self._combined_classify_line(line_words)
            if classified is None:
                continue
            kind, payload = classified
            if kind == "stop":
                break  # reserved; combined layout no longer emits "stop" for Total rows
            if kind == "anchor":
                payload["y"] = y
                anchors.append(payload)
            elif kind == "continuation":
                continuations.append({"y": y, "text": payload})

        return self._build_combined_transactions(anchors, continuations)

    def _combined_classify_line(
        self, line_words: list[dict]
    ) -> tuple[str, Any] | None:
        """Return ('anchor', dict), ('continuation', str), ('stop', None), or None."""
        joined = " ".join(w["text"] for w in line_words)
        jl = joined.lower()
        # Section summary row (PPF + Savings on one page). Do **not** end the page —
        # older code returned "stop" here, which dropped the entire Savings table below.
        if re.match(r"^\s*total\s*:", jl, re.IGNORECASE):
            return None
        for w in line_words:
            low = w["text"].strip().lower()
            if low.startswith("total:") or low == "total:":
                return None
        if "date" in jl and "particulars" in jl and "withdrawals" in jl:
            return None

        # Anchor: DD-MM-YYYY in the left column (may be glued to MODE text).
        date_val: datetime.date | None = None
        extra_from_date = ""
        for w in line_words:
            if w["x0"] >= _COMBINED_DATE_PREFIX_MAX:
                break
            text = w["text"].strip()
            m = re.match(r"^(\d{2}-\d{2}-\d{4})(.*)$", text)
            if not m:
                continue
            ds, rest = m.group(1), m.group(2).strip()
            date_val = self._parse_date_dmy(ds)
            if rest:
                extra_from_date = rest
            break

        remark_parts: list[tuple[float, str]] = []
        for w in line_words:
            x = w["x0"]
            if _COMBINED_PARTICULARS_MIN <= x < _COMBINED_PARTICULARS_MAX:
                remark_parts.append((x, w["text"]))

        inline = " ".join(t for _, t in sorted(remark_parts))
        if extra_from_date:
            inline = f"{extra_from_date} {inline}".strip()

        deposit: Decimal | None = None
        withdrawal: Decimal | None = None
        for w in line_words:
            x = w["x0"]
            if x >= _COMBINED_BALANCE_X_MIN:
                continue
            amt = self._parse_amount(w["text"])
            if amt is None:
                continue
            lo, hi = _COMBINED_DEPOSIT_X
            if lo <= x < hi:
                deposit = amt
            lo2, hi2 = _COMBINED_WITHDRAWAL_X
            if lo2 <= x < hi2:
                withdrawal = amt

        if date_val is not None:
            skip = inline.strip().upper() in {"B/F", "B/F*"} or (
                deposit is None and withdrawal is None
            )
            return (
                "anchor",
                {
                    "date": date_val,
                    "inline_remarks": inline,
                    "withdrawal": withdrawal,
                    "deposit": deposit,
                    "skip": skip,
                },
            )

        # Continuation: particulars only (no date column).
        if remark_parts:
            return ("continuation", inline.strip())
        return None

    def _build_combined_transactions(
        self,
        anchors: list[dict],
        continuations: list[dict],
    ) -> list[ParsedTransaction]:
        """Stitch combined-layout anchors + continuation lines (same midpoint idea as legacy)."""
        results: list[ParsedTransaction] = []

        for i, anchor in enumerate(anchors):
            if anchor.get("skip"):
                continue
            ya = anchor["y"]
            prev_y = anchors[i - 1]["y"] if i > 0 else 0
            next_y = anchors[i + 1]["y"] if i + 1 < len(anchors) else float("inf")
            prev_mid = (prev_y + ya) / 2
            next_mid = (ya + next_y) / 2

            prefix_parts = [
                c["text"]
                for c in continuations
                if prev_mid < c["y"] < ya
            ]
            suffix_parts = [
                c["text"]
                for c in continuations
                if ya < c["y"] < next_mid
            ]
            full_description = " ".join(
                filter(None, prefix_parts + [anchor["inline_remarks"]] + suffix_parts)
            ).strip()

            if not full_description:
                continue

            withdrawal = anchor.get("withdrawal")
            deposit = anchor.get("deposit")
            if withdrawal is None and deposit is None:
                continue

            debit_amount = withdrawal if withdrawal is not None else Decimal("0")
            credit_amount = deposit if deposit is not None else Decimal("0")
            if debit_amount == 0 and credit_amount == 0:
                continue

            try:
                results.append(
                    ParsedTransaction(
                        txn_date=anchor["date"],
                        raw_description=full_description,
                        debit_amount=debit_amount,
                        credit_amount=credit_amount,
                        closing_balance=None,
                        metadata={},
                    )
                )
            except Exception as exc:
                warnings.warn(
                    f"[icici_savings combined] row skipped: {exc}",
                    stacklevel=2,
                )

        return results

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
    def _parse_date_dmy(s: str) -> datetime.date | None:
        """Parse DD-MM-YYYY (combined email-statement layout)."""
        try:
            return datetime.datetime.strptime(s.strip(), "%d-%m-%Y").date()
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
