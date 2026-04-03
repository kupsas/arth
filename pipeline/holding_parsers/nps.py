"""
NPS CRA statements → **one** holding per PRAN (no separate E / C / G rows).

- **Current value:** CRA summary ``[A]`` / “Value of your Holdings…” when present,
  else sum of scheme-wise value columns (E, C, G blocks).
- **Your cost (principal):** summary ``(B)`` / “Total Contribution in your account…”
  when present, else sum of **Employee Contribution** rows under
  “Contribution/Redemption Details During the Selected Period”.
- **Transactions:** only ``NPS employee contribution`` BUY rows (audit trail); we do
  not emit per-scheme unit ledger lines.

**Sanity:** If the statement ``as on`` date is after ``reference_date`` (default:
today UTC), we skip emitting the holding snapshot (no forward-dated values).
Contribution rows dated in the future are still skipped individually.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, date, datetime
from pathlib import Path

from pipeline.holding_parsers.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    parse_indian_amount,
    strip_bom,
)
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod

PLATFORM = "NPS (CRA)"
# Stable name for ingest matching + ``find_existing_holding`` (PRAN).
NPS_CANONICAL_HOLDING_NAME = "National Pension System (NPS)"


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _parse_stmt_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _clean_pran_from_text(block: str) -> str | None:
    m = re.search(r"PRAN\D*['\s]?(\d{12})\b", block, re.I)
    if m:
        return m.group(1)
    m2 = re.search(r"\b(\d{12})\b", block)
    if m2 and "PRAN" in block.upper():
        return m2.group(1)
    return None


def _norm_hdr(cell: str) -> str:
    return re.sub(r"\s+", " ", (cell or "").strip().lower())


def _strip_rs_prefix(s: str) -> str:
    return re.sub(r"^(rs\.?|₹|inr)\s*", "", (s or "").strip(), flags=re.I)


def _amounts_in_csv_line(line: str) -> list[float]:
    try:
        parts = next(csv.reader(io.StringIO(line)))
    except (StopIteration, csv.Error):
        return []
    out: list[float] = []
    for p in parts:
        p2 = _strip_rs_prefix(p.strip())
        if not p2:
            continue
        try:
            v = parse_indian_amount(p2)
            if v > 0:
                out.append(v)
        except ValueError:
            continue
    return out


def _parse_nps_summary_totals(lines: list[str]) -> tuple[float | None, float | None]:
    """CRA summary: total value [A] and total contribution (B), if present."""
    val_a: float | None = None
    val_b: float | None = None
    for line in lines[:280]:
        lu = line.upper()
        if "[A]" in lu or (
            "VALUE" in lu and "HOLDING" in lu and ("INVESTMENT" in lu or "INVESTMENTS" in lu)
        ):
            nums = _amounts_in_csv_line(line)
            if nums:
                m = max(nums)
                val_a = m if val_a is None else max(val_a, m)
        if "(B)" in line or "[B]" in lu or (
            "TOTAL" in lu and "CONTRIBUTION" in lu and "ACCOUNT" in lu and "NOTIONAL" not in lu
        ):
            nums = _amounts_in_csv_line(line)
            if nums:
                m = max(nums)
                val_b = m if val_b is None else max(val_b, m)
    return val_a, val_b


def _fallback_investment_summary_ab(lines: list[str]) -> tuple[float | None, float | None]:
    """
    CRA exports often put (A)/(B) labels on one row and rupee figures on the next.
    Typical data row: ``Rs 132870.69,4,Rs 105193.20,...`` → value A, contribution count, B.
    Skips lines whose first CSV cell is a statement date (contribution ledger rows).
    """
    for line in lines[:50]:
        try:
            first_cell = next(csv.reader(io.StringIO(line)))[0]
        except (StopIteration, csv.Error, IndexError):
            first_cell = ""
        if _parse_stmt_date((first_cell or "").strip()):
            continue
        nums = _amounts_in_csv_line(line)
        if len(nums) >= 3 and nums[0] >= 1_000 and nums[2] >= 1_000:
            return round(nums[0], 2), round(nums[2], 2)
        if len(nums) >= 2 and nums[0] >= 1_000 and nums[1] >= 1_000:
            return round(nums[0], 2), round(nums[1], 2)
    return None, None


def _parse_month_first_statement_date(m: re.Match[str]) -> date | None:
    """Parse ``Month D[,] YYYY`` groups from a month-first regex match."""
    mon, d_s, y_s = m.group(1), int(m.group(2)), m.group(3)
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(f"{mon} {d_s} {y_s}", fmt).date()
        except ValueError:
            continue
    return None


def _parse_dmy_after_phrase(m: re.Match[str]) -> date | None:
    """Parse ``D-Mon-YYYY`` groups from a DMY regex match."""
    d_s, mon, y_s = int(m.group(1)), m.group(2), m.group(3)
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(f"{d_s}-{mon}-{y_s}", fmt).date()
        except ValueError:
            continue
    return None


def _statement_as_on_max(lines: list[str]) -> date | None:
    """Latest CRA statement date from the header.

    CRA CSVs use phrases like **\"as on March 23 2026\"** (sometimes glued to the
    previous cell, e.g. ``...)as on March 23 2026``). A few exports say **\"as of\"**
    instead — we accept both so the snapshot date is never dropped.

    We scan a **joined blob** of the first ~220 lines so the phrase still matches
    if line breaks fall between words (rare) or the row is one long CSV line.
    """
    found: list[date] = []
    # Month-first: "as on March 23 2026" / "as of Mar 23, 2026"
    pat_on_mf = re.compile(
        r"as\s+on\s+([A-Za-z]{3,})\s+(\d{1,2})[,]?\s+(\d{4})",
        re.I,
    )
    pat_of_mf = re.compile(
        r"as\s+of\s+([A-Za-z]{3,})\s+(\d{1,2})[,]?\s+(\d{4})",
        re.I,
    )
    pat_dmy_on = re.compile(
        r"as\s+on\s+(\d{1,2})[-/]([A-Za-z]{3,})[-/](\d{4})",
        re.I,
    )
    pat_dmy_of = re.compile(
        r"as\s+of\s+(\d{1,2})[-/]([A-Za-z]{3,})[-/](\d{4})",
        re.I,
    )

    blob = "\n".join(lines[:220])
    for m in pat_on_mf.finditer(blob):
        d = _parse_month_first_statement_date(m)
        if d is not None:
            found.append(d)
    for m in pat_of_mf.finditer(blob):
        d = _parse_month_first_statement_date(m)
        if d is not None:
            found.append(d)
    for m in pat_dmy_on.finditer(blob):
        d = _parse_dmy_after_phrase(m)
        if d is not None:
            found.append(d)
    for m in pat_dmy_of.finditer(blob):
        d = _parse_dmy_after_phrase(m)
        if d is not None:
            found.append(d)

    return max(found) if found else None


def _parse_nps_contribution_section(
    lines: list[str],
    ref_date: date,
    pran: str | None,
    fname: str,
) -> list[ParsedInvestmentTxn]:
    out: list[ParsedInvestmentTxn] = []
    i = 0
    n = len(lines)
    while i < n:
        lnl = lines[i].lower()
        if not ("contribution" in lnl and "redemption" in lnl and "selected period" in lnl):
            i += 1
            continue

        header_j: int | None = None
        colmap: dict[str, int] = {}
        for j in range(i + 1, min(i + 35, n)):
            try:
                row = next(csv.reader(io.StringIO(lines[j])))
            except (StopIteration, csv.Error):
                continue
            if len(row) < 4:
                continue
            heads = [_norm_hdr(c) for c in row]
            if not any(h.startswith("date") for h in heads):
                continue
            if not any("particulars" in h for h in heads):
                continue
            colmap.clear()
            for idx, h in enumerate(heads):
                if h.startswith("date"):
                    colmap.setdefault("date", idx)
                if "particulars" in h:
                    colmap["particulars"] = idx
                if "upload" in h:
                    colmap["uploaded_by"] = idx
                if "employee" in h and "contribution" in h:
                    colmap["employee"] = idx
                if "employer" in h and "contribution" in h:
                    colmap["employer"] = idx
                if h.startswith("total") and "notional" not in h and "gain" not in h and "withdrawal" not in h:
                    colmap.setdefault("total_rs", idx)
            if "date" in colmap and "employee" in colmap:
                header_j = j
                break

        if header_j is None:
            i += 1
            continue

        di, ei = colmap["date"], colmap["employee"]
        pi = colmap.get("particulars")
        ui = colmap.get("uploaded_by")

        k = header_j + 1
        seen_contrib_row = False
        while k < n:
            raw = lines[k].strip()
            # Blank line between header and first data row is common — skip until we see data.
            if not raw:
                if seen_contrib_row:
                    break
                k += 1
                continue
            try:
                drow = next(csv.reader(io.StringIO(lines[k])))
            except (StopIteration, csv.Error):
                k += 1
                continue
            if di >= len(drow):
                k += 1
                continue
            d0 = _parse_stmt_date(drow[di])
            if not d0:
                k += 1
                continue
            if d0 > ref_date:
                k += 1
                continue

            emp_s = _strip_rs_prefix(drow[ei].strip() if ei < len(drow) else "")
            try:
                emp = parse_indian_amount(emp_s) if emp_s else 0.0
            except ValueError:
                k += 1
                continue

            if emp <= 0:
                k += 1
                continue

            particulars = drow[pi].strip() if pi is not None and pi < len(drow) else ""
            uploaded = drow[ui].strip() if ui is not None and ui < len(drow) else ""
            notes_line = " | ".join(x for x in (particulars, uploaded) if x)
            pr_line = f"PRAN {pran}" if pran else ""
            notes_full = "\n".join(x for x in (notes_line, pr_line) if x)

            out.append(
                ParsedInvestmentTxn(
                    txn_date=d0,
                    symbol=None,
                    name="NPS employee contribution",
                    txn_type=InvestmentTxnType.BUY.value,
                    quantity=1.0,
                    price_per_unit=emp,
                    total_amount=emp,
                    account_platform=PLATFORM,
                    notes=notes_full or None,
                    metadata={
                        "source_file": fname,
                        "pran": pran or "",
                        "contrib_section": True,
                    },
                )
            )
            seen_contrib_row = True
            k += 1

        i = k
    return out


def _scheme_snapshot_key_and_value(ln: str) -> tuple[str, float] | None:
    """
    CRA scheme line → (dedupe key, rupee value). Legacy ``E,1,2,3`` → key E/C/G.
    """
    up = ln.upper()
    if ("SCHEME E" in up or "SCHEME C" in up or "SCHEME G" in up) and "TIER" in up:
        if "PARTICULARS" in up and "SCHEME WISE" in up:
            return None
        try:
            row = next(csv.reader(io.StringIO(ln)))
        except (StopIteration, csv.Error):
            return None
        row = [c.strip() for c in row]
        while row and row[-1] == "":
            row.pop()
        if len(row) < 4:
            return None
        head = row[0].strip()
        if head.lower().startswith("particulars"):
            return None
        if _parse_stmt_date(head):
            return None
        try:
            value = parse_indian_amount(row[-3])
        except ValueError:
            return None
        if value <= 0:
            return None
        name = ",".join(row[:-3]).strip() or head
        return (name[:256], value)

    parts = [p.strip() for p in re.split(r",|\t", ln) if p.strip() != ""]
    if len(parts) < 4:
        return None
    tag = parts[0].upper()
    if tag not in ("E", "C", "G"):
        return None
    try:
        value = parse_indian_amount(parts[1])
    except ValueError:
        return None
    if value <= 0:
        return None
    return (f"LEGACY_{tag}", value)


def parse_nps_statement(
    path: Path,
    *,
    reference_date: date | None = None,
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    ref_date = reference_date if reference_date is not None else _utc_today()
    text = strip_bom(path.read_text(encoding="utf-8", errors="replace"))
    lines = [ln.rstrip() for ln in text.splitlines()]

    head = "\n".join(lines[:40])
    pran = _clean_pran_from_text(head)

    as_of_max = _statement_as_on_max(lines)
    skip_holdings = as_of_max is not None and as_of_max > ref_date

    summary_a, summary_b = _parse_nps_summary_totals(lines)
    if summary_a is None or summary_b is None:
        fa, fb = _fallback_investment_summary_ab(lines)
        summary_a = summary_a or fa
        summary_b = summary_b or fb

    scheme_totals: dict[str, float] = {}
    for ln in lines:
        kv = _scheme_snapshot_key_and_value(ln)
        if kv:
            k, v = kv
            scheme_totals[k] = v
    summed_schemes = round(sum(scheme_totals.values()), 2) if scheme_totals else None

    contrib_txns = _parse_nps_contribution_section(lines, ref_date, pran, path.name)
    contrib_sum = round(sum(t.total_amount for t in contrib_txns), 2) if contrib_txns else None

    total_cv = summary_a if summary_a is not None else summed_schemes
    total_principal = summary_b if summary_b is not None else contrib_sum
    if total_principal is not None and total_principal <= 0:
        total_principal = None
    if total_principal is not None:
        total_principal = round(total_principal, 2)

    holdings: list[ParsedHolding] = []
    if not skip_holdings and total_cv is not None and total_cv > 0:
        holdings.append(
            ParsedHolding(
                symbol=None,
                name=NPS_CANONICAL_HOLDING_NAME,
                quantity=None,
                asset_class=AssetClass.NPS.value,
                valuation_method=ValuationMethod.MANUAL.value,
                account_platform=PLATFORM,
                current_value=round(total_cv, 2),
                principal_amount=total_principal,
                liquidity_class=LiquidityClass.ILLIQUID.value,
                folio_number=pran,
                metadata={
                    "source_file": path.name,
                    "pran": pran or "",
                    "value_as_of_date": as_of_max.isoformat() if as_of_max is not None else "",
                    "snapshot_value": round(total_cv, 2),
                },
            )
        )

    contrib_txns.sort(key=lambda t: t.txn_date)
    return holdings, contrib_txns


class NPSParser(BaseHoldingParser):
    @property
    def source_id(self) -> str:
        return "nps"

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        p = Path(path)
        all_h: list[ParsedHolding] = []
        all_t: list[ParsedInvestmentTxn] = []
        if p.is_file():
            return parse_nps_statement(p)
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.suffix.lower() == ".csv":
                    h, t = parse_nps_statement(f)
                    all_h.extend(h)
                    all_t.extend(t)
        return all_h, all_t
