"""
SBI PPF / ICICI PPF style CSV: skip metadata rows 1–10; header on row 11.

Classification (matches bank CSV):
- ``Int.Pd`` / interest in remarks → DIVIDEND (not your out-of-pocket principal).
- Other deposits → BUY (your contributions).
- Withdrawals → SELL.

Rows dated after ``reference_date`` (default: today UTC) are skipped so we never
book forward-dated ledger lines. ``principal_amount`` on the holding is net
contributions from parsed txns: sum(BUY) − sum(SELL). Opening-balance rows stay
excluded from txns (carry-forward); if you need that in principal, import a
longer history or set ``principal_amount`` manually once.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

from pipeline.holding_parsers.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    parse_indian_amount,
    strip_bom,
)
from pipeline.models import AssetClass, CompoundingFrequency, InvestmentTxnType, LiquidityClass, ValuationMethod
from pipeline.ppf_maturity import ppf_statutory_maturity_date

# Government PPF rate — update when statement shows a new rate (parser does not scrape RBI).
PPF_RATE_ANNUAL_DEFAULT = 7.1


def _utc_today() -> date:
    return datetime.now(UTC).date()


def parse_icici_ppf_csv(
    path: Path,
    *,
    account_platform: str = "ICICI PPF",
    reference_date: date | None = None,
) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    raw_lines = strip_bom(path.read_text(encoding="utf-8", errors="replace")).splitlines()
    # Data starts at line index 10 (row 11) per plan; find header row containing "Transaction Date"
    start_idx = 0
    for i, line in enumerate(raw_lines):
        if "Transaction Date" in line and "Deposit" in line:
            start_idx = i
            break

    text = "\n".join(raw_lines[start_idx:])
    reader = csv.DictReader(line.strip() for line in text.splitlines() if line.strip())
    if not reader.fieldnames:
        return [], []

    ref = reference_date if reference_date is not None else _utc_today()
    txns: list[ParsedInvestmentTxn] = []
    balance = 0.0

    for row in reader:
        # Normalise keys
        rk = {strip_bom((k or "").strip()): v for k, v in row.items()}
        date_s = (rk.get("Transaction Date") or "").strip()
        remarks = (rk.get("Transaction Remarks") or "").strip()
        dep_s = rk.get("Deposit Amount (INR )") or rk.get("Deposit Amount (INR)") or ""
        wdr_s = rk.get("Withdrawal Amount (INR )") or rk.get("Withdrawal Amount (INR)") or ""
        bal_s = rk.get("Balance (INR )") or rk.get("Balance (INR)") or ""

        if not date_s:
            continue
        try:
            dt = datetime.strptime(date_s, "%d-%b-%Y").date()
        except ValueError:
            try:
                dt = datetime.strptime(date_s, "%d/%m/%Y").date()
            except ValueError:
                continue

        if dt > ref:
            # Do not import or roll forward balances from future-dated rows.
            continue

        dep = parse_indian_amount(str(dep_s))
        wdr = parse_indian_amount(str(wdr_s))
        if bal_s:
            balance = parse_indian_amount(str(bal_s))

        remarks_l = remarks.lower()
        if "opening" in remarks_l and dep > 0:
            continue
        is_interest = "int.pd" in remarks_l or "interest" in remarks_l
        if dep > 0 and not is_interest:
            txns.append(
                ParsedInvestmentTxn(
                    txn_date=dt,
                    symbol=None,
                    name="PPF contribution",
                    txn_type=InvestmentTxnType.BUY.value,
                    quantity=1.0,
                    price_per_unit=dep,
                    total_amount=dep,
                    account_platform=account_platform,
                    notes=remarks or None,
                    metadata={"source_file": path.name},
                )
            )
        elif is_interest and dep > 0:
            txns.append(
                ParsedInvestmentTxn(
                    txn_date=dt,
                    symbol=None,
                    name="PPF interest",
                    txn_type=InvestmentTxnType.DIVIDEND.value,
                    quantity=1.0,
                    price_per_unit=dep,
                    total_amount=dep,
                    account_platform=account_platform,
                    notes=remarks or None,
                    metadata={"source_file": path.name},
                )
            )
        elif wdr > 0:
            txns.append(
                ParsedInvestmentTxn(
                    txn_date=dt,
                    symbol=None,
                    name="PPF withdrawal",
                    txn_type=InvestmentTxnType.SELL.value,
                    quantity=1.0,
                    price_per_unit=wdr,
                    total_amount=wdr,
                    account_platform=account_platform,
                    notes=remarks or None,
                    metadata={"source_file": path.name},
                )
            )

    txns.sort(key=lambda t: t.txn_date)

    # Deployed principal (your money only): contributions minus withdrawals.
    # DIVIDEND rows are bank interest — excluded from this sum.
    buy_sum = sum(t.total_amount for t in txns if t.txn_type == InvestmentTxnType.BUY.value)
    sell_sum = sum(t.total_amount for t in txns if t.txn_type == InvestmentTxnType.SELL.value)
    net_principal = round(buy_sum - sell_sum, 2)
    principal = net_principal if net_principal > 0 else None

    first_buy = next(
        (t.txn_date for t in txns if t.txn_type == InvestmentTxnType.BUY.value),
        None,
    )
    ppf_maturity = ppf_statutory_maturity_date(first_buy) if first_buy else None

    holding = ParsedHolding(
        symbol=None,
        name="Public Provident Fund (PPF)",
        quantity=None,
        asset_class=AssetClass.PPF.value,
        valuation_method=ValuationMethod.FIXED_RETURN.value,
        account_platform=account_platform,
        current_value=balance if balance else None,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        principal_amount=principal,
        interest_rate=PPF_RATE_ANNUAL_DEFAULT,
        compounding_frequency=CompoundingFrequency.ANNUALLY.value,
        maturity_date=ppf_maturity,
        metadata={"source_file": path.name},
    )
    return [holding], txns


class ICICIPPFParser(BaseHoldingParser):
    @property
    def source_id(self) -> str:
        return "icici_ppf"

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        p = Path(path)
        if p.is_file():
            return parse_icici_ppf_csv(p)
        if p.is_dir():
            all_h: list[ParsedHolding] = []
            all_t: list[ParsedInvestmentTxn] = []
            for f in sorted(p.iterdir()):
                if f.suffix.lower() == ".csv":
                    h, t = parse_icici_ppf_csv(f)
                    all_h.extend(h)
                    all_t.extend(t)
            return all_h, all_t
        return [], []
