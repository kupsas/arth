"""
Parser for SBI **e-account statement** (Composite Account Statement / CAS) PDFs.

These arrive as password-protected email attachments from ``cbssbi.cas@alerts.sbi.bank.in``
(or the legacy ``.co.in`` domain). The PDF is multi-page: portfolio summary first, then
``TRANSACTION DETAILS`` sections per savings account.

Transaction table columns (measured from real CAS PDFs):
  Date (DD-MM-YY) | Transaction Reference | Ref.No./Chq.No. | Credit | Debit | Balance

Parsing strategy: pdfplumber word-level extraction with bounding boxes — same idea as
:class:`~parsers.uploads.icici_savings.ICICISavingsParser`. Long UPI narrations wrap to
extra lines; we stitch prefix/suffix continuation rows onto anchor rows.
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
from parsers.uploads.base import BaseParser

# Column x-coordinate boundaries (from empirical measurement on CAS PDFs).
_X_DATE_MAX = 55
_X_REF_MIN = 55
_X_REF_MAX = 360
_X_CREDIT_MIN = 390
_X_CREDIT_MAX = 465
_X_DEBIT_MIN = 465
_X_DEBIT_MAX = 528
_X_BALANCE_MIN = 528

_DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")
_ACCOUNT_MASK_RE = re.compile(r"X{3,}(\d{4})\b", re.IGNORECASE)
_Y_BUCKET_PX = 4

_STOP_MARKERS = {"visithttps://sbi.co.in", "customer carenumber:"}

# Last row on a page often glues the txn narration to the period closing-balance line.
_DESC_FOOTER_TAIL_RE = re.compile(
    r"\s+Balance on \d{2}-\d{2}-\d{2}:.*$",
    re.IGNORECASE,
)
# Continuation-only legal/footer lines (not transactions).
_DESC_FOOTER_ONLY_RE = re.compile(
    r"^(balance on \d{2}-\d{2}-\d{2}:|format of this statement will|contents of this statement)",
    re.IGNORECASE,
)


def _line_rows_from_page(page: Any) -> list[tuple[float, str, list[dict]]]:
    """Sorted (y-bucket, joined text, line_words) for one page."""
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


class SBISavingsParser(BaseParser):
    """Extract savings cash transactions from SBI CAS (e-account statement) PDFs."""

    @property
    def source_id(self) -> str:
        return "sbi_savings"

    def parse(self, file_path: str | Path) -> list[ParsedTransaction]:
        path = Path(file_path)
        rows: list[ParsedTransaction] = []
        with pdfplumber.open(path) as pdf:
            current_account_last4: str | None = None
            in_txn_section = False
            for page in pdf.pages:
                page_rows, current_account_last4, in_txn_section = self._parse_page(
                    page,
                    current_account_last4=current_account_last4,
                    in_txn_section=in_txn_section,
                )
                rows.extend(page_rows)
        return sorted(rows, key=lambda r: (r.metadata.get("account_last4", ""), r.txn_date))

    def _parse_page(
        self,
        page: Any,
        *,
        current_account_last4: str | None,
        in_txn_section: bool,
    ) -> tuple[list[ParsedTransaction], str | None, bool]:
        lines = _line_rows_from_page(page)
        rows: list[ParsedTransaction] = []
        anchors: list[dict] = []
        continuations: list[dict] = []
        in_txn_details = in_txn_section
        awaiting_acct_line = False
        parsing_table = False
        acct_last4 = current_account_last4

        for y, txt, line_words in lines:
            low = txt.lower()
            if any(m in low.replace(" ", "") for m in _STOP_MARKERS):
                break

            if "customer x" in low.replace(" ", ""):
                continue

            if "transaction details" in low:
                in_txn_details = True
                awaiting_acct_line = False
                parsing_table = False
                anchors = []
                continuations = []
                continue

            if not in_txn_details:
                continue

            # Section header is its own line — not the page-1 summary row
            # ``Transaction Accounts* SAVING ACCOUNT INR …``.
            if re.match(r"^saving account\s*$", low):
                awaiting_acct_line = True
                parsing_table = False
                anchors = []
                continuations = []
                continue

            if awaiting_acct_line:
                m_acct = _ACCOUNT_MASK_RE.search(txt)
                if m_acct:
                    acct_last4 = m_acct.group(1)
                    awaiting_acct_line = False
                continue

            if "transaction overview" in low:
                parsing_table = True
                anchors = []
                continuations = []
                continue

            if not parsing_table or acct_last4 is None:
                continue

            if "date transaction reference" in low:
                anchors = []
                continuations = []
                continue

            classified = self._classify_line(line_words, txt)
            if classified is None:
                continue
            kind, payload = classified
            if kind == "anchor":
                payload["y"] = y
                anchors.append(payload)
            elif kind == "continuation":
                continuations.append({"y": y, "text": payload})

        page_rows = self._build_transactions(anchors, continuations, acct_last4 or "")
        rows.extend(page_rows)
        return rows, acct_last4, in_txn_details

    def _classify_line(
        self, line_words: list[dict], joined: str
    ) -> tuple[str, Any] | None:
        """Return ('anchor', dict), ('continuation', str), or None."""
        jl = joined.lower()
        if "opening balance" in jl or "oupllening" in jl or "nballlance" in jl:
            return None
        if re.search(r"\bnull\b", jl):
            return None

        date_val: datetime.date | None = None
        ref_parts: list[tuple[float, str]] = []
        credit: Decimal | None = None
        debit: Decimal | None = None
        balance: Decimal | None = None

        for w in line_words:
            x = w["x0"]
            text = w["text"].strip()
            if not text:
                continue

            if x < _X_DATE_MAX and _DATE_RE.match(text):
                date_val = self._parse_date_dmy(text)
                continue

            if _X_REF_MIN <= x < _X_REF_MAX and text != "-":
                ref_parts.append((x, text))
            elif _X_CREDIT_MIN <= x < _X_CREDIT_MAX:
                amt = self._parse_amount(text)
                if amt is not None:
                    credit = amt
            elif _X_DEBIT_MIN <= x < _X_DEBIT_MAX:
                amt = self._parse_amount(text)
                if amt is not None:
                    debit = amt
            elif x >= _X_BALANCE_MIN:
                amt = self._parse_amount(text)
                if amt is not None:
                    balance = amt

        inline_ref = " ".join(t for _, t in sorted(ref_parts)).strip()

        if date_val is not None:
            if credit is None and debit is None:
                return None
            return (
                "anchor",
                {
                    "date": date_val,
                    "inline_ref": inline_ref,
                    "credit": credit,
                    "debit": debit,
                    "balance": balance,
                },
            )

        if inline_ref and date_val is None:
            if _DESC_FOOTER_ONLY_RE.match(inline_ref.strip()):
                return None
            return ("continuation", inline_ref)

        return None

    @staticmethod
    def _clean_description(desc: str) -> str:
        """Drop SBI page-footer tails accidentally merged into the narration column."""
        cleaned = _DESC_FOOTER_TAIL_RE.sub("", desc).strip()
        if _DESC_FOOTER_ONLY_RE.match(cleaned):
            return ""
        return cleaned

    def _build_transactions(
        self,
        anchors: list[dict],
        continuations: list[dict],
        account_last4: str,
    ) -> list[ParsedTransaction]:
        results: list[ParsedTransaction] = []

        for i, anchor in enumerate(anchors):
            ya = anchor["y"]
            prev_y = anchors[i - 1]["y"] if i > 0 else 0
            next_y = anchors[i + 1]["y"] if i + 1 < len(anchors) else float("inf")
            prev_mid = (prev_y + ya) / 2
            next_mid = (ya + next_y) / 2

            prefix_parts = [
                self._clean_description(c["text"])
                for c in continuations
                if prev_mid < c["y"] < ya
            ]
            suffix_parts = [
                self._clean_description(c["text"])
                for c in continuations
                if ya < c["y"] < next_mid
            ]
            prefix_parts = [p for p in prefix_parts if p]
            suffix_parts = [p for p in suffix_parts if p]
            full_description = " ".join(
                filter(None, prefix_parts + [anchor["inline_ref"]] + suffix_parts)
            ).strip()
            if not full_description or full_description == "-":
                full_description = anchor["inline_ref"] or " ".join(prefix_parts).strip()
            full_description = self._clean_description(full_description)
            if not full_description or full_description == "-":
                continue

            credit = anchor.get("credit") or Decimal("0")
            debit = anchor.get("debit") or Decimal("0")
            if credit == 0 and debit == 0:
                continue

            try:
                results.append(
                    ParsedTransaction(
                        txn_date=anchor["date"],
                        raw_description=full_description,
                        debit_amount=debit,
                        credit_amount=credit,
                        closing_balance=anchor.get("balance"),
                        metadata={"account_last4": account_last4},
                    )
                )
            except Exception as exc:
                warnings.warn(
                    f"[sbi_savings] row skipped for acct …{account_last4}: {exc}",
                    stacklevel=2,
                )

        return results

    @staticmethod
    def _parse_date_dmy(s: str) -> datetime.date | None:
        """Parse DD-MM-YY (SBI CAS uses two-digit year)."""
        try:
            dt = datetime.datetime.strptime(s.strip(), "%d-%m-%y").date()
            return dt
        except ValueError:
            return None

    @staticmethod
    def _parse_amount(s: str) -> Decimal | None:
        s = s.strip().replace(",", "")
        if not s or s == "-":
            return None
        try:
            val = Decimal(s)
            return val if val >= 0 else None
        except InvalidOperation:
            return None
