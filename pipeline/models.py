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


class SpendCategory(str, Enum):
    """Macro classification of what a spend is going towards.

    NEED       — essential living expense (rent, utilities, healthcare, transport)
    WANT       — discretionary spending (dining, entertainment, shopping, travel)
    INVESTMENT — capital deployed into markets (equity, MF, SIP) or moved to
                 own savings / FD (money parked for future use)

    Only meaningful for OUTFLOW transactions. INFLOW rows remain NULL.
    FRIENDS_FAMILY transfers are intentionally left NULL — they are internal
    or family-support transactions, not a standard spend category.
    """
    NEED = "NEED"
    WANT = "WANT"
    INVESTMENT = "INVESTMENT"


class ClassificationSource(str, Enum):
    """Where the latest automated classification pass attributed this row."""

    RULES_GENERIC = "RULES_GENERIC"   # starter pack or non-personal heuristics
    RULES_USER = "RULES_USER"         # DB-backed user merchant / contact rules
    LLM = "LLM"
    USER_REVIEWED = "USER_REVIEWED"     # set when the user edits a stored row (API)


# ---------------------------------------------------------------------------
# Asset / portfolio enums (Phase A.0 — Layer 1 net worth)
# Stored as strings in SQLite; use these values in API and parsers.
# ---------------------------------------------------------------------------


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    MUTUAL_FUND = "MUTUAL_FUND"
    FD = "FD"
    PPF = "PPF"
    NPS = "NPS"
    SAVINGS = "SAVINGS"
    GOLD = "GOLD"
    SOVEREIGN_GOLD_BOND = "SOVEREIGN_GOLD_BOND"
    REAL_ESTATE = "REAL_ESTATE"
    ESOP = "ESOP"
    OTHER = "OTHER"


class ValuationMethod(str, Enum):
    MARKET_PRICE = "MARKET_PRICE"
    FIXED_RETURN = "FIXED_RETURN"
    MANUAL = "MANUAL"


class LiquidityClass(str, Enum):
    INSTANT = "INSTANT"
    T_PLUS_1 = "T_PLUS_1"
    T_PLUS_3 = "T_PLUS_3"
    WEEKS = "WEEKS"
    ILLIQUID = "ILLIQUID"


class InvestmentTxnType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    SIP = "SIP"
    SWITCH_IN = "SWITCH_IN"
    SWITCH_OUT = "SWITCH_OUT"


class LiabilityType(str, Enum):
    SECURED_LOAN = "SECURED_LOAN"
    UNSECURED_LOAN = "UNSECURED_LOAN"
    REVOLVING_CREDIT = "REVOLVING_CREDIT"
    RECURRING_OBLIGATION = "RECURRING_OBLIGATION"


class CompoundingFrequency(str, Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    HALF_YEARLY = "HALF_YEARLY"
    ANNUALLY = "ANNUALLY"


class MutualFundType(str, Enum):
    GROWTH = "GROWTH"
    DIVIDEND = "DIVIDEND"
    IDCW = "IDCW"


# ---------------------------------------------------------------------------
# Goals architecture (Sub-Plan A) — stored as strings in SQLite
# ---------------------------------------------------------------------------


class GoalClass(str, Enum):
    POINT_IN_TIME = "POINT_IN_TIME"
    RECURRING_CASH_FLOW = "RECURRING_CASH_FLOW"


class GoalSubtype(str, Enum):
    HOME_PURCHASE = "HOME_PURCHASE"
    VEHICLE = "VEHICLE"
    WEDDING = "WEDDING"
    RETIREMENT = "RETIREMENT"
    CHILD_EDUCATION = "CHILD_EDUCATION"
    EMERGENCY_FUND = "EMERGENCY_FUND"
    TRAVEL = "TRAVEL"
    LOAN_PAYOFF = "LOAN_PAYOFF"
    CUSTOM = "CUSTOM"


class RecurrenceFrequency(str, Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"


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
    spend_category: SpendCategory | None = None   # NEED / WANT / INVESTMENT  (NULL for INFLOW + Friends & Family)
    classification_source: ClassificationSource | None = None

    # --- Raw / audit ---
    raw_description: str
    ref_number: str | None = None
    closing_balance: Decimal | None = None
    value_date: datetime.date | None = None
    notes: str | None = None
