"""
Parser for HDFC credit card statement CSVs.

Handles all four variants that exist in the real data:
  - Card 1905 (old format, Jan-Aug 2025)   — tilde delimiter, 6 columns
  - Card 5778 (old format, Jan-Aug 2025)   — tilde delimiter, 7 columns (extra reward points)
  - Card 1905 (new format, Sep-Dec 2025)   — ~|~ delimiter, 6 columns + trailing empty
  - Card 5778 (new format, Sep-Dec 2025)   — ~|~ delimiter, 7 columns (REWARDS at end)

Format detection: if the first non-empty line contains "~|~" it's the new format.

Column layout is read dynamically from the header row so the extra reward-points
column on 5778 is handled transparently — no hardcoded column indices.

The parser can accept either a single CSV file or a directory of CSVs (one per
billing cycle). In directory mode it merges all files and sorts by txn_date so
downstream code gets a clean, time-ordered stream regardless of file order.
"""

from __future__ import annotations

import datetime
import logging
import re
import warnings
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pipeline.detection import DetectionResult, PARSER_LABELS
from pipeline.models import ParsedTransaction
from pipeline.parsers.base import BaseParser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column name constants  (normalised — stripped and lowercased for matching)
# ---------------------------------------------------------------------------
_COL_TXN_TYPE = "transaction type"
_COL_DATE = "date"
_COL_DESCRIPTION = "description"
_COL_AMT = "amt"
_COL_DEBIT_CREDIT = "debit /credit"   # old: "debit / credit", new: "debit /credit"

# The reward-points column exists only on 5778 (both formats use different names)
_COL_REWARD_OLD = "feature reward points"
_COL_REWARD_NEW = "rewards"

_CC_LAST4_PATTERNS = (
    re.compile(r"(?:X{4}\s*){3}(\d{4})", re.I),
    re.compile(
        r"(?:Card|CARD)\s*(?:No\.?|Number)?\s*[:\s]*.*?(\d{4})\s*(?:\n|$)",
        re.S,
    ),
)


def _hdfc_cc_last4_hint(sample: str) -> str | None:
    for rx in _CC_LAST4_PATTERNS:
        m = rx.search(sample)
        if m:
            return m.group(1)
    for line in sample.splitlines()[:40]:
        if "hdfc" in line.lower() and re.search(r"\b(\d{4})\b", line):
            m2 = re.search(r"\b(\d{4})\b", line)
            if m2:
                return m2.group(1)
    return None


class HDFCCreditCardParser(BaseParser):
    """Parse HDFC credit card statements (single file or full-year directory)."""

    @property
    def source_id(self) -> str:
        return "hdfc_cc"

    @classmethod
    def detect(cls, file_path: str | Path) -> DetectionResult | None:
        """HDFC CC CSV: tilde-delimited rows; header row starts with *Transaction type*."""
        path = Path(file_path)
        if path.is_dir():
            csvs = sorted(path.glob("*.csv"))
            if not csvs:
                return None
            path = csvs[0]
        elif path.suffix.lower() != ".csv":
            return None

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        first_content = next((ln for ln in lines if ln.strip()), "")
        if "~" not in first_content and "~|~" not in first_content:
            return None
        delimiter = "~|~" if "~|~" in first_content else "~"

        def split_row(raw: str) -> list[str]:
            return [c.strip() for c in raw.strip().split(delimiter)]

        header_idx = None
        for i, line in enumerate(lines):
            cells = split_row(line)
            if cells and cells[0].lower() == "transaction type":
                header_idx = i
                break
        if header_idx is None:
            return None

        sample = "\n".join(lines[: min(len(lines), 60)])
        hint = _hdfc_cc_last4_hint(sample)
        return DetectionResult(
            source_type="hdfc_cc",
            confidence=0.94,
            account_hint=hint,
            label=PARSER_LABELS["hdfc_cc"],
        )

    def parse(self, file_path: str | Path) -> list[ParsedTransaction]:
        """Accept either a single .csv file or a directory of monthly .csv files.

        In directory mode, all files are parsed and the results are merged and
        sorted chronologically before being returned.
        """
        path = Path(file_path)

        if path.is_dir():
            rows: list[ParsedTransaction] = []
            # Sort glob results so January comes before December etc.
            for csv_file in sorted(path.glob("*.csv")):
                rows.extend(self._parse_file(csv_file))
            # Final sort by date so the caller always gets a clean time series.
            return sorted(rows, key=lambda r: r.txn_date)

        return self._parse_file(path)

    # ------------------------------------------------------------------
    # Per-file parsing
    # ------------------------------------------------------------------

    def _parse_file(self, file_path: Path) -> list[ParsedTransaction]:
        """Parse a single monthly CC CSV and return a list of ParsedTransactions."""
        with open(file_path, encoding="utf-8") as fh:
            lines = fh.readlines()

        # ── Step 1: detect delimiter ──────────────────────────────────────
        # Look at the first non-empty line to decide which format this is.
        first_content = next((line for line in lines if line.strip()), "")
        is_new_format = "~|~" in first_content
        delimiter = "~|~" if is_new_format else "~"

        def split_row(raw: str) -> list[str]:
            """Split on the format delimiter and strip each cell."""
            return [cell.strip() for cell in raw.strip().split(delimiter)]

        # ── Step 2: find the transaction header row ───────────────────────
        # Scan until we hit a row whose first cell is "Transaction type".
        # This skips the address block, account summary, and past-dues tables.
        header_idx: int | None = None
        for i, line in enumerate(lines):
            cells = split_row(line)
            if cells and cells[0].lower() == "transaction type":
                header_idx = i
                break

        if header_idx is None:
            warnings.warn(
                f"[hdfc_cc] No transaction header row found in {file_path.name} — "
                "skipping file.",
                stacklevel=2,
            )
            return []

        # ── Step 3: build column name → index map ─────────────────────────
        # Using the header row to map column names to their positions handles
        # the 5778 reward-points column automatically without branching here.
        header_cells = split_row(lines[header_idx])
        col_index: dict[str, int] = {}
        for idx, cell in enumerate(header_cells):
            normalised = cell.lower().strip()
            if normalised:
                col_index[normalised] = idx

        # Validate that mandatory columns are present
        mandatory = [_COL_TXN_TYPE, _COL_DATE, _COL_DESCRIPTION, _COL_AMT]
        for col in mandatory:
            # Debit/Credit header has a spacing difference between old and new format
            # so we look for the normalised debit-credit column separately below.
            if col not in col_index:
                warnings.warn(
                    f"[hdfc_cc] Missing mandatory column '{col}' in "
                    f"{file_path.name} — skipping file.",
                    stacklevel=2,
                )
                return []

        # Find the debit/credit column — normalised to handle spacing variation
        # old: "Debit / Credit"  new: "Debit /Credit"
        dc_col_key = next(
            (k for k in col_index if "debit" in k and "credit" in k), None
        )
        if dc_col_key is None:
            warnings.warn(
                f"[hdfc_cc] Missing Debit/Credit column in {file_path.name} — "
                "skipping file.",
                stacklevel=2,
            )
            return []

        # Optional reward-points column (only 5778)
        reward_col_key = next(
            (k for k in col_index if "reward" in k or k == _COL_REWARD_NEW),
            None,
        )

        # ── Step 4: parse data rows until end marker ──────────────────────
        rows: list[ParsedTransaction] = []

        for raw_line in lines[header_idx + 1:]:
            line = raw_line.strip()

            # End of transactions: blank line signals the footer section
            # (old: "Opening Bal~Earned~..."  new: "Reward Points Summary")
            if not line:
                break

            cells = split_row(raw_line)

            # Skip sub-header or section divider lines that sneak through
            if not cells or cells[0].lower() in ("transaction type", ""):
                continue

            parsed = self._parse_row(
                cells=cells,
                col_index=col_index,
                dc_col_key=dc_col_key,
                reward_col_key=reward_col_key,
                filename=file_path.name,
            )
            if parsed is not None:
                rows.append(parsed)

        return rows

    # ------------------------------------------------------------------
    # Single-row parsing
    # ------------------------------------------------------------------

    def _parse_row(
        self,
        cells: list[str],
        col_index: dict[str, int],
        dc_col_key: str,
        reward_col_key: str | None,
        filename: str,
    ) -> ParsedTransaction | None:
        """Extract one ParsedTransaction from a list of cell values."""

        def get(col_key: str) -> str:
            """Safe cell accessor — returns empty string for out-of-bounds."""
            idx = col_index.get(col_key)
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx].strip()

        txn_type_raw = get(_COL_TXN_TYPE)          # "Domestic" / "International"
        date_str = get(_COL_DATE)
        description = get(_COL_DESCRIPTION)
        amt_str = get(_COL_AMT)
        dc_raw = cells[col_index[dc_col_key]].strip() if col_index[dc_col_key] < len(cells) else ""

        reward_pts: str | None = None
        if reward_col_key:
            reward_pts = get(reward_col_key) or None

        # ── Validate row has minimum data ─────────────────────────────────
        if not date_str or not description or not amt_str:
            return None

        # ── Parse date ────────────────────────────────────────────────────
        # Both formats use DD/MM/YYYY; new format always appends HH:MM:SS.
        txn_date = self._parse_date(date_str, filename)
        if txn_date is None:
            return None

        # ── Parse amount ──────────────────────────────────────────────────
        # Indian thousands notation: "1,09,819.00" → 109819.00
        amount = self._parse_amount(amt_str)
        if amount is None or amount == Decimal("0"):
            return None

        # ── Map Debit / Credit direction ──────────────────────────────────
        # "Cr" in the cell means the bank is crediting your account (inflow):
        # cashback, refunds, CC bill payments received.
        # Blank or space means a debit (outflow) — a purchase.
        if "cr" in dc_raw.lower():
            debit_amount = Decimal("0")
            credit_amount = amount
        else:
            debit_amount = amount
            credit_amount = Decimal("0")

        # ── Build metadata ────────────────────────────────────────────────
        metadata: dict = {
            "domestic_or_international": txn_type_raw.lower(),  # "domestic" / "international"
            "channel_hint": "CARD",
        }
        if reward_pts:
            # Strip leading "+" and spaces so "+ 308" becomes "308"
            metadata["reward_points"] = reward_pts.lstrip("+ ").strip()

        # Clean description — old format has fixed-width space padding
        description = " ".join(description.split())

        return ParsedTransaction(
            txn_date=txn_date,
            raw_description=description,
            debit_amount=debit_amount,
            credit_amount=credit_amount,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(s: str, filename: str) -> datetime.date | None:
        """Parse DD/MM/YYYY or DD/MM/YYYY HH:MM:SS into a date object.

        Both old and new format use DD/MM/YYYY; the new format always adds
        a time component. We try the long form first then fall back.
        """
        s = s.strip()
        if not s:
            return None
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        logger.warning("hdfc_cc: could not parse date %r in %s", s, filename)
        return None

    @staticmethod
    def _parse_amount(s: str) -> Decimal | None:
        """Parse an Indian-formatted amount string like '1,09,819.00' → Decimal."""
        s = s.strip().replace(",", "")
        if not s:
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
