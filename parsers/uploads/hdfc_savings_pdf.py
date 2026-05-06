"""
Parser for **HDFC Bank Combined Email Statement** PDFs (password-protected attachment).

The downloadable ``.txt`` export uses a different layout — see :class:`HDFCSavingsParser`.
This module handles the **PDF** produced by monthly combined email statements
(``Txn Date | Narration | Withdrawals | Deposits | Closing Balance``), using
pdfplumber **word coordinates** the same way :mod:`parsers.uploads.icici_savings`
does for ICICI emailed PDFs.

Why word-level extraction?
  ``extract_tables()`` often misses data rows; ``extract_text()`` loses column boundaries
  when narration wraps across lines.  Words carry ``x0``/``top`` so we can bucket lines,
  detect **anchor** rows (left-column date + amount columns), and attach **continuation**
  lines (wrapped narration, ``Value Dt``, ``Ref``) to the correct transaction.

Layout (measured from real statements, 2024–2026):
  - **Txn date** — ``DD/MM/YYYY`` at ``x0`` ≈ 52–55 (must be the *left* date column;
    continuation lines may show a date at ``x`` ≈ 111 — those are *not* anchors).
  - **Narration** — roughly ``78 ≤ x0 < 295``.
  - **Withdrawals** — ``295 ≤ x0 < 385``.
  - **Deposits** — ``385 ≤ x0 < 495``.
  - **Closing balance** — ``x0 ≥ 495`` (parsed for debugging only; not emitted as txn amount).

We stop when we hit the monthly **SUMMARY** block or ``*** End of Statement ***``.
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

# ── Line bucketing (same idea as ICICI: small px tolerance groups words on one visual row)
_Y_BUCKET_PX = 3

# Left column: only dates here count as *transaction* anchors (not "Ref" continuation lines).
_X_LEFT_DATE_MAX = 78
# Narration band (between date column and amount columns)
_X_NARR_MIN = 78
_X_NARR_MAX = 295
# Amount columns — balance is to the right of deposits; do not treat as debit/credit
_X_WITHDRAWAL = (295, 385)
_X_DEPOSIT = (385, 495)

_DATE_DDMMYYYY = re.compile(r"^\d{2}/\d{2}/\d{4}$")


class HDFCSavingsPdfParser(BaseParser):
    """Parse HDFC combined monthly savings PDFs into :class:`ParsedTransaction` rows."""

    @property
    def source_id(self) -> str:
        return "hdfc_savings_pdf"

    @classmethod
    def detect(cls, file_path: str | Path) -> DetectionResult | None:
        """Combined HDFC savings PDF: relationship summary + savings txn grid."""
        path = Path(file_path)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            return None
        try:
            with pdfplumber.open(path) as pdf:
                inst = cls()
                if not pdf.pages:
                    return None
                ok = False
                for i in range(min(3, len(pdf.pages))):
                    t = pdf.pages[i].extract_text() or ""
                    tl = t.lower()
                    if "credit card" in tl and "combined" not in tl and "savings account details" not in tl:
                        continue
                    if "Combined" in t or "Savings Account Details" in t:
                        ok = True
                        break
                    if "Txn Date" in t and ("Withdrawals" in t or "withdrawal" in tl or "Deposits" in t):
                        ok = True
                        break
                if not ok:
                    ok = inst._looks_like_combined_statement_pdf(pdf)
                if not ok:
                    return None
        except Exception:
            return None
        return DetectionResult(
            source_type="hdfc_savings_pdf",
            confidence=0.88,
            account_hint=None,
            label=PARSER_LABELS["hdfc_savings_pdf"],
        )

    def parse(self, file_path: str | Path) -> list[ParsedTransaction]:
        path = Path(file_path)
        if path.is_dir():
            rows: list[ParsedTransaction] = []
            for pdf_file in sorted(path.glob("*.pdf")):
                rows.extend(self._parse_file(pdf_file))
            return sorted(rows, key=lambda r: r.txn_date)
        return self._parse_file(path)

    def _parse_file(self, file_path: Path) -> list[ParsedTransaction]:
        rows: list[ParsedTransaction] = []
        with pdfplumber.open(file_path) as pdf:
            if not self._looks_like_combined_statement_pdf(pdf):
                warnings.warn(
                    f"[hdfc_savings_pdf] {file_path.name}: does not look like a combined "
                    "statement PDF (missing expected headers).",
                    stacklevel=2,
                )
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    rows.extend(self._parse_page(page, page_num))
                except Exception as exc:
                    warnings.warn(
                        f"[hdfc_savings_pdf] page {page_num} of {file_path.name}: {exc}",
                        stacklevel=2,
                    )
        return rows

    def _looks_like_combined_statement_pdf(self, pdf: Any) -> bool:
        """Heuristic so we fail loudly on wrong PDF type.

        Page 1 is often **only** the relationship summary — the strings we need may
        appear on page 2+, so scan a few pages.
        """
        if not pdf.pages:
            return False
        for i in range(min(3, len(pdf.pages))):
            t = pdf.pages[i].extract_text() or ""
            if "Combined" in t or "Savings Account Details" in t or "Txn Date" in t:
                return True
        return False

    def _parse_page(self, page: Any, page_num: int) -> list[ParsedTransaction]:
        words = page.extract_words()
        y_groups: dict[int, list[dict]] = defaultdict(list)
        for word in words:
            bucket = round(word["top"] / _Y_BUCKET_PX) * _Y_BUCKET_PX
            y_groups[bucket].append(word)

        sorted_y = sorted(y_groups.keys())

        # Only rows *below* the column header — otherwise address/IFSC lines become
        # continuations merged into the first real transaction.
        header_y: float | None = None
        for y in sorted_y:
            line_words = sorted(y_groups[y], key=lambda w: w["x0"])
            joined = " ".join(w["text"] for w in line_words).lower()
            if self._is_table_header_line(joined):
                header_y = y
                break

        if header_y is None:
            return []

        anchors: list[dict[str, Any]] = []
        continuations: list[dict[str, Any]] = []

        for y in sorted_y:
            if y <= header_y:
                continue
            line_words = sorted(y_groups[y], key=lambda w: w["x0"])
            joined = " ".join(w["text"] for w in line_words)
            jl = joined.lower()

            if self._should_skip_noise_line(jl, line_words):
                continue

            classified = self._classify_line(line_words, jl)
            if classified is None:
                continue
            kind, payload = classified
            if kind == "stop":
                break
            if kind == "anchor":
                payload["y"] = y
                anchors.append(payload)
            elif kind == "continuation":
                continuations.append({"y": y, "text": payload})

        return self._build_transactions(anchors, continuations)

    def _is_table_header_line(self, jl: str) -> bool:
        return (
            "txn date" in jl
            and "narration" in jl
            and ("withdrawals" in jl or "withdrawal" in jl)
        )

    def _should_skip_noise_line(self, jl: str, line_words: list[dict]) -> bool:
        """Skip page headers, account metadata, opening balance banner — not txn rows."""
        if "savings account details" in jl:
            return True
        if "opening balance" in jl and "limit" in jl:
            return True
        if jl.startswith("page ") and " of " in jl:
            return True
        if "account number" in jl:
            return True
        if "customer id" in jl:
            return True
        if "joint holders" in jl:
            return True
        if "statement from" in jl and " to " in jl and re.search(r"\d{2}/\d{2}/\d{4}", jl):
            return True
        if "nomination" in jl and "ifsc" in jl:
            return True
        if "account type" in jl and "saving" in jl:
            return True
        if re.match(r"^currency\s*:\s*inr", jl.strip(), re.I):
            return True
        # Relationship summary on page 1
        if "account relationship summary" in jl:
            return True
        if "total withdrawable balance" in jl and "does not include" in jl:
            return True
        return False

    def _classify_line(
        self, line_words: list[dict], jl: str
    ) -> tuple[str, Any] | None:
        """Return ('anchor', dict), ('continuation', str), ('stop', None), or None."""

        if jl.strip() == "summary" or jl.startswith("summary "):
            return ("stop", None)
        if "*** end of statement ***" in jl.replace(" ", ""):
            return ("stop", None)
        if "debit count" in jl and "credit count" in jl:
            return ("stop", None)
        if "your combined statement generation frequency" in jl:
            return ("stop", None)

        left_date = self._left_column_date(line_words)
        withdrawal, deposit = self._amounts_in_columns(line_words)

        narr_parts: list[tuple[float, str]] = []
        for w in line_words:
            if _X_NARR_MIN <= w["x0"] < _X_NARR_MAX:
                narr_parts.append((w["x0"], w["text"]))
        inline = " ".join(t for _, t in sorted(narr_parts))

        if left_date is not None:
            # Real txn row: must have at least one non-zero amount in W/D columns
            if withdrawal is None and deposit is None:
                return None
            wd = withdrawal or Decimal("0")
            dp = deposit or Decimal("0")
            if wd == 0 and dp == 0:
                return None
            return (
                "anchor",
                {
                    "date": left_date,
                    "inline_remarks": inline,
                    "withdrawal": withdrawal,
                    "deposit": deposit,
                },
            )

        # Continuation: wrapped narration, Value Dt / Ref lines (no left-column date)
        if not narr_parts:
            # Still capture pure continuation that only has words past narration (e.g. refs)
            fallback = " ".join(w["text"] for w in line_words if w["x0"] >= _X_NARR_MIN)
            if len(fallback.strip()) < 4:
                return None
            return ("continuation", fallback.strip())

        return ("continuation", inline.strip())

    def _left_column_date(self, line_words: list[dict]) -> datetime.date | None:
        """First DD/MM/YYYY in the *left* date column only."""
        for w in line_words:
            if w["x0"] >= _X_LEFT_DATE_MAX:
                break
            t = w["text"].strip()
            if _DATE_DDMMYYYY.match(t):
                return self._parse_date_slash(t)
        return None

    def _amounts_in_columns(
        self, line_words: list[dict]
    ) -> tuple[Decimal | None, Decimal | None]:
        w_lo, w_hi = _X_WITHDRAWAL
        d_lo, d_hi = _X_DEPOSIT
        withdrawal: Decimal | None = None
        deposit: Decimal | None = None
        for w in line_words:
            x = w["x0"]
            amt = self._parse_amount_token(w["text"])
            if amt is None:
                continue
            if w_lo <= x < w_hi:
                withdrawal = amt
            elif d_lo <= x < d_hi:
                deposit = amt
        return withdrawal, deposit

    @staticmethod
    def _parse_amount_token(s: str) -> Decimal | None:
        s = s.strip().replace(",", "")
        if not s or not re.match(r"^-?[\d.]+$", s):
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None

    @staticmethod
    def _parse_date_slash(s: str) -> datetime.date | None:
        try:
            return datetime.datetime.strptime(s, "%d/%m/%Y").date()
        except ValueError:
            return None

    def _build_transactions(
        self,
        anchors: list[dict[str, Any]],
        continuations: list[dict[str, Any]],
    ) -> list[ParsedTransaction]:
        """Merge continuation lines into anchors.

        HDFC packs **Value Dt / Ref / wrapped narration** tightly between two txn rows.
        A vertical midpoint split (as in ICICI) wrongly assigns trailing lines of txn *N*
        to txn *N+1*.  Here every continuation strictly **between** this anchor and the
        next anchor's y-position is stitched as a **suffix** of the current txn only.
        """
        results: list[ParsedTransaction] = []

        for i, anchor in enumerate(anchors):
            ya = anchor["y"]
            next_y = anchors[i + 1]["y"] if i + 1 < len(anchors) else float("inf")

            between = [c for c in continuations if ya < c["y"] < next_y]
            between.sort(key=lambda c: c["y"])
            suffix = [c["text"] for c in between]

            desc = " ".join(filter(None, [anchor["inline_remarks"]] + suffix)).strip()
            if not desc:
                continue

            wd = anchor.get("withdrawal") or Decimal("0")
            dp = anchor.get("deposit") or Decimal("0")

            results.append(
                ParsedTransaction(
                    txn_date=anchor["date"],
                    raw_description=desc,
                    debit_amount=wd,
                    credit_amount=dp,
                    closing_balance=None,
                    metadata={},
                )
            )

        return results
