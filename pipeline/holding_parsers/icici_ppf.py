"""
SBI PPF / ICICI PPF style CSV: skip metadata rows 1–10; header on row 11.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from pipeline.holding_parsers.base import (
    BaseHoldingParser,
    ParsedHolding,
    ParsedInvestmentTxn,
    parse_indian_amount,
    strip_bom,
)
from pipeline.models import AssetClass, CompoundingFrequency, InvestmentTxnType, LiquidityClass, ValuationMethod

# Government PPF rate — update when statement shows a new rate (parser does not scrape RBI).
PPF_RATE_ANNUAL_DEFAULT = 7.1


def parse_icici_ppf_csv(path: Path, *, account_platform: str = "ICICI PPF") -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
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

    holding = ParsedHolding(
        symbol=None,
        name="Public Provident Fund (PPF)",
        quantity=None,
        asset_class=AssetClass.PPF.value,
        valuation_method=ValuationMethod.FIXED_RETURN.value,
        account_platform=account_platform,
        current_value=balance if balance else None,
        liquidity_class=LiquidityClass.ILLIQUID.value,
        interest_rate=PPF_RATE_ANNUAL_DEFAULT,
        compounding_frequency=CompoundingFrequency.ANNUALLY.value,
        metadata={"source_file": path.name},
    )
    return [holding], txns


class ICICIPPFParser(BaseHoldingParser):
    @property
    def source_id(self) -> str:
        return "icici_ppf"

    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        p = Path(path)
        if not p.is_file():
            return [], []
        return parse_icici_ppf_csv(p)
