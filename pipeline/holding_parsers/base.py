"""
Holding / investment parser framework (Phase A.2.1).

Bank statement parsers emit ``ParsedTransaction``; portfolio parsers emit
``ParsedHolding`` and/or ``ParsedInvestmentTxn``. Downstream
``holding_pipeline`` validates, encrypts PII, and upserts into SQLite.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pipeline.detection import DetectionResult


class ParsedHolding(BaseModel):
    """Normalized holding row produced by any portfolio / statement parser."""

    symbol: str | None = None
    isin: str | None = None
    name: str
    quantity: float | None = None
    asset_class: str  # AssetClass value
    valuation_method: str  # ValuationMethod value
    account_platform: str
    average_cost_per_unit: float | None = None
    current_value: float | None = None
    current_price_per_unit: float | None = None
    liquidity_class: str  # LiquidityClass value
    folio_number: str | None = None
    fund_type: str | None = None
    amfi_scheme_code: str | None = None
    principal_amount: float | None = None
    interest_rate: float | None = None
    maturity_date: date | None = None
    compounding_frequency: str | None = None
    face_value: float | None = None
    coupon_rate: float | None = None
    coupon_frequency: str | None = None
    is_active: bool = True
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False}


class ParsedInvestmentTxn(BaseModel):
    """Normalized broker / fund ledger row."""

    txn_date: date
    symbol: str | None = None
    name: str | None = None
    txn_type: str  # InvestmentTxnType value
    quantity: float
    price_per_unit: float
    total_amount: float
    account_platform: str
    brokerage: float | None = None
    charges: float | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False}


class ParsedLiability(BaseModel):
    """Row for seeding ``api.models.Liability`` (bike loan, insurance premium, etc.)."""

    name: str
    liability_type: str  # LiabilityType value
    principal_outstanding: float
    interest_rate: float = 0.0
    emi_amount: float | None = None
    tenure_remaining_months: int | None = None
    emi_start_date: date | None = None
    emi_end_date: date | None = None
    notes: str | None = None


class BaseHoldingParser(ABC):
    """Plug-in base: one class per institution / file family."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Registry key, e.g. ``icici_direct_equity``."""
        ...

    @abstractmethod
    def parse_path(self, path: str | Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
        """Read *path* (file or directory) and return (holdings, investment_txns)."""
        ...

    @classmethod
    def detect(cls, path: str | Path) -> "DetectionResult | None":
        """Return detection metadata if *path* looks like this CSV/directory layout."""
        return None


def parse_icici_number(raw: str) -> float:
    """Parse ICICI CSV numbers: commas, parentheses for negatives, ``- 4.91`` style."""
    s = (raw or "").strip()
    if not s or s == "-":
        return 0.0
    neg_paren = s.startswith("(") and s.endswith(")")
    if neg_paren:
        s = s[1:-1].strip()
    # Normalise spaced minus (e.g. "% Change" column)
    s = re.sub(r"-\s+", "-", s)
    s = s.replace(",", "").replace(" ", "")
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg_paren else v


def parse_indian_amount(raw: str) -> float:
    """Strip grouping commas then float (PPF / NPS tables)."""
    s = (raw or "").strip().replace(",", "")
    if not s or s == "-":
        return 0.0
    return float(s)


def strip_bom(s: str) -> str:
    return s.lstrip("\ufeff")


__all__ = [
    "BaseHoldingParser",
    "ParsedHolding",
    "ParsedInvestmentTxn",
    "ParsedLiability",
    "parse_icici_number",
    "parse_indian_amount",
    "strip_bom",
]
