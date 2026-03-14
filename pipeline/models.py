"""
Pydantic models for the raw-to-canonical pipeline.

Two models live here:

1. ParsedTransaction — the **bank-agnostic intermediate**.  Every parser
   (HDFC savings, ICICI, credit card, …) must produce a list of these.
   Downstream code never sees bank-specific quirks.

2. CanonicalTransaction — the **fully enriched output row**.  This is what
   the transformer + classifiers produce and what gets written to CSV / DB.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums  (derived from the manually-corrected GSheet ground truth)
# ---------------------------------------------------------------------------

class Direction(str, Enum):
    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"


class TxnType(str, Enum):
    BANK_TRANSFER = "BANK_TRANSFER"
    CARD_EXPENSE = "CARD_EXPENSE"          # individual credit-card swipe / purchase
    CARD_PAYMENT = "CARD_PAYMENT"          # paying the CC bill from savings account
    EQUITY_PURCHASE = "EQUITY_PURCHASE"   # buying equities via ICICI Direct (EBA/EQ Trade OUTFLOW)
    EQUITY_SALE = "EQUITY_SALE"           # selling equities / eATM redemption (EBA/EQ Trade INFLOW)
    EXPENSE_OTHER = "EXPENSE_OTHER"
    INCOME_DIVIDEND = "INCOME_DIVIDEND"   # dividend credited via ACH from company
    INCOME_OTHER = "INCOME_OTHER"
    INCOME_SALARY = "INCOME_SALARY"
    LOAN_INSURANCE_PAYMENT = "LOAN_INSURANCE_PAYMENT"
    MF_PURCHASE = "MF_PURCHASE"           # mutual fund purchase (EBA/MFP OUTFLOW)
    MF_SALE = "MF_SALE"                   # MF redemption proceeds credited (NEFT from fund house)
    SELF_TRANSFER = "SELF_TRANSFER"
    UPI_EXPENSE = "UPI_EXPENSE"
    UPI_TRANSFER = "UPI_TRANSFER"


class Channel(str, Enum):
    UPI = "UPI"
    UPI_LITE = "UPI-LITE"
    BANK = "BANK"
    CARD = "CARD"
    BROKER = "BROKER"


class UPIType(str, Enum):
    P2P = "P2P"
    P2M = "P2M"
    LITE_SELF_FUND = "LITE_SELF_FUND"
    NA = "NA"


class CounterpartyCategory(str, Enum):
    ASSET_MARKETS = "Asset Markets"        # equities, MFs, dividends via ICICI Direct
    ENTERTAINMENT_EVENTS = "Entertainment & Events"
    FEES_CHARGES_INTEREST = "Fees, Charges & Interest"
    FINANCIAL_SERVICES = "Financial Services, Insurance & Banking"
    FOOD_DINING = "Food & Dining"
    FRIENDS_FAMILY = "Friends and Family"
    GIFTS_PERSONAL_TRANSFERS = "Gifts & Personal Transfers"
    HEALTHCARE_PHARMACY = "Healthcare & Pharmacy"
    MISCELLANEOUS = "Miscellaneous"
    MOBILE_OTT_SUBSCRIPTIONS = "Mobile, OTT & Subscriptions"
    PERSONAL_GROOMING = "Personal Grooming"
    RENT_HOUSING = "Rent & Housing"
    SALARY_INCOME = "Salary & Income"
    SELF_TRANSFER = "Self Transfer"
    SHOPPING_ECOMMERCE = "Shopping & E-commerce"
    SWIGGY = "Swiggy"
    TRANSPORT_FUEL = "Transport & Fuel"
    TRAVEL_STAY = "Travel & Stay"
    UTILITIES_INTERNET = "Utilities & Internet"


# ---------------------------------------------------------------------------
# ParsedTransaction  (intermediate — what every parser produces)
# ---------------------------------------------------------------------------

class ParsedTransaction(BaseModel):
    """Bank-agnostic row produced by any parser.

    Only contains fields that *every* statement format can provide.
    Source-specific extras go into ``metadata``.
    """

    txn_date: datetime.date
    raw_description: str = Field(min_length=1)

    # Exactly one of these should be >0; the other should be 0.
    debit_amount: Decimal = Field(ge=Decimal("0"))
    credit_amount: Decimal = Field(ge=Decimal("0"))

    # Optional fields — not all sources have them
    ref_number: str | None = None
    closing_balance: Decimal | None = None
    value_date: datetime.date | None = None

    # Catch-all for source-specific extras (CC domestic/intl flag, etc.)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_amount_nonzero(self) -> "ParsedTransaction":
        """At least one of debit/credit must be positive."""
        if self.debit_amount == 0 and self.credit_amount == 0:
            raise ValueError(
                "Both debit_amount and credit_amount are zero — "
                "every transaction must have a non-zero amount"
            )
        return self

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# CanonicalTransaction  (fully enriched output row)
# ---------------------------------------------------------------------------

class CanonicalTransaction(BaseModel):
    """The final, fully enriched transaction row.

    Fields are populated in stages:
    - transformer fills identity + amount fields
    - rules_classifier fills channel, txn_type (partial), upi_type (partial)
    - llm_classifier fills counterparty, counterparty_category, remaining gaps
    """

    # --- Identity ---
    txn_id: str = Field(pattern=r"^T_\d{8}$")
    txn_date: datetime.date
    account_id: str
    source_statement: str

    # --- Amount ---
    direction: Direction
    amount: Decimal = Field(gt=Decimal("0"))
    currency: str = "INR"

    # --- Classification (filled progressively by rules then LLM) ---
    txn_type: TxnType | None = None
    channel: Channel | None = None
    upi_type: UPIType | None = None
    counterparty: str | None = None
    counterparty_category: CounterpartyCategory | None = None

    # --- Raw / audit ---
    raw_description: str
    ref_number: str | None = None
    closing_balance: Decimal | None = None
    value_date: datetime.date | None = None
    notes: str | None = None
