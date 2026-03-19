"""
Deterministic rules classifier.

Fills ``channel``, ``txn_type``, and ``upi_type`` on CanonicalTransaction
using pattern-matching on the raw_description (bank narration).

Design:
  - Classify as much as possible here to minimise LLM calls and cost.
  - Rules derived from inspecting the 647-row GSheet ground truth.
  - Where the rules can't decide (e.g. UPI_EXPENSE vs UPI_TRANSFER,
    or BANK_TRANSFER vs SELF_TRANSFER), leave the field as None so
    the LLM classifier can fill it.
"""

from __future__ import annotations

import re

from pipeline.models import (
    CanonicalTransaction,
    Channel,
    CounterpartyCategory,
    Direction,
    SpendCategory,
    TxnType,
    UPIType,
)

# ---------------------------------------------------------------------------
# Self-transfer indicators (user's own name / aliases)
# ---------------------------------------------------------------------------
_SELF_INDICATORS = [
    "SASHANK",
    "MEICICI",         # HDFC's alias for the user's ICICI-linked account
    "SANDOZ",          # ICICI savings alias used for recurring transfers
]

_FAMILY_NAMES = [
    "KUPPA ADI LAKSHMI",
    "KUPPA SRINIVASA MURTHY",       # appears truncated as "KUPPA SRINIVASA MURT" in UPI
    "KUPPA VENKATA VINOD KRISHNA",  # appears as "VENKATA VINOD KRISHNA KUPPA" in RDA
]

# Friends → counterparty_category = "Friends and Family"
_FRIENDS_NAMES = [
    "ARYAN KUKREJA",
    "ADITI ABHAY LOTLIKAR",
    "SANYAM JAIN",
    "DEVYANI MODI",
    "KUSHAGRA MISHRA",
    "STUTI SINGH",
    "RUDDHI PRASAD PANDA",
    "ANUBHAV PANDEY",
    "VARANASI SHASHANK",
    "Chinmay Bhatt",   # ICICI BIL/INFT transfers
]

# Acquaintances → counterparty_category = "Gifts & Personal Transfers"
_ACQUAINTANCES_NAMES = [
    "NIMISH GUPTA",
    "NASEEMA BEGUM",
    "ASHLESHA NAOKARKAR",
    "CHINMAY VYAS",
    "SIDDHANT NARULA",
    "K V RAMA KRISHNA",
    "ANUJ KUMAR",
    "DARIEN SAVIO RODRIGUEZ",
    "SHANTI DEVI",
    "RITHU PAUL",
    "SAMIKSHA GURBAXANI",
    "SANJANA JAIN",
    "SATYANSH RAI",
    "BABUL HUSSAIN LASKAR",
    "SHAH AAHAN USHIR",
    "ABBEY ZACHARIAH GEORGE",
    "LAXMI RUTVIK REDDY",
    "SAJITH KRISHNAA",
    "SAHANA NAGARAJA MUDLAPUR"
]

# Business keywords in UPI names — prevents Uber heuristic from firing on merchants.
# Matching is truncation-safe (prefix comparison) so "PHARMAC" matches "PHARMACY".
_MERCHANT_NAME_KEYWORDS = [
    "PHARMACY", "MEDICAL", "HOSPITAL", "CLINIC", "DENTAL", "HEALTH",
    "COFFEE", "RESTAURANT", "HOTEL", "FOOD", "KITCHEN", "BAKERY", "CAFE",
    "DINING", "DINER", "EATCLUB", "CANTEEN",
    "LIMITED", "PVT", "LTD", "INDIA", "CORPORATION", "ENTERPRISE",
    "STORE", "SHOP", "AGENCY", "SERVICE", "SERVICES",
    "TELECOM", "FIBER", "PREPAID", "POSTPAID",
    "BAZAAR", "MART", "MARKET", "MALL",
    "INSURANCE", "BANK",
    "SWIGGY", "ZOMATO", "AMAZON", "FLIPKART",
    "INFOCOM", "ONLINE", "GAS", "FUEL", "PETROL",
    "CINEMA", "NEXUS", "GROOMING", "SALON", "STYLE", "SMILE",
    "LLC", "INC",
    "BUS", "BMTC",
    "TOUR", "TRAVEL", "RESORT", "LODGE", "HOSTEL",
    "GOIBIBO", "MAKEMYTRIP", "IRCTC", "CLEARTRIP", "YATRA",
    "CAR DECOR", "ELECTRIC", "HARDWARE", "PLUMB",
    "LIQUOR", "WINE", "SPIRITS",
    "CLOTH", "WEAR", "FASHION", "APPAREL",
    "RAZORPAY",
    "ENSHAF KHAN",  # private LPG provider
]

# Hotel/tour/travel keywords — UPI names matching these get Travel & Stay category
# (word-based prefix matching, truncation-safe e.g. "TRAV" matches "TRAVEL")
_HOTEL_TRAVEL_KEYWORDS = frozenset({
    "HOTEL", "RESORT", "INN", "LODGE", "HOSTEL", "HOMESTAY",
    "TOUR", "TRAVEL",
    "GOIBIBO", "MAKEMYTRIP", "IRCTC", "CLEARTRIP", "YATRA",
    "HMSHOST", "AIRBNB",
})

# ---------------------------------------------------------------------------
# UPI handle-based P2P / P2M detection
# ---------------------------------------------------------------------------
# Derived from analysing all 648 ground-truth transactions.  Only patterns
# that appear *exclusively* in one category are used — ambiguous handles
# (e.g. @PTYS, @PAYTM, @YBL which Uber drivers also use) are left for the LLM.

# Bank/PSP suffixes (the part after @) that are *exclusively* P2M
_P2M_BANK_SUFFIXES = frozenset({
    "HDFCBANK",    # HDFC merchant onboarding
    "AXISBANK",    # Axis merchant accounts
    "PTYBL",       # PayTM Business Link
    "REL",         # Reliance / Jio
    "RXAIRTEL",    # Razorpay via Airtel
    "RXAXIS",      # Razorpay via Axis
    "OKPAYAXIS",   # Axis merchant payment gateway
    "CITIBANK",    # Citibank merchant
    "APL",         # Amazon Pay
    "RAPL",        # Amazon
    "FBPE",        # BharatPe (Federal Bank)
    "UNITYPE",     # BharatPe (Unity)
    "YESBANK",     # Yes Bank merchant
    "YESBANKLTD",  # Yes Bank (full suffix variant, e.g. BharatPe, YesPay)
    "SBIPAY",      # SBI Pay merchant
    "AXB",         # Axis Bank business
    "PINEAXIS",    # Pine Labs / Axis
    "CMSIDFC",     # CMS IDFC
    "SC",          # Standard Chartered
    "INDUS",       # IndusInd Bank merchant
    "JKB",         # J&K Bank merchant
})

# Bank/PSP suffixes that are *exclusively* P2P
_P2P_BANK_SUFFIXES = frozenset({
    "PTYES",       # PayTM personal (Yes Bank)
    "PTHDFC",      # PayTM personal (HDFC)
    "PTAXIS",      # PayTM personal (Axis)
    "PTSBI",       # PayTM personal (SBI)
    "OKSBI",       # SBI personal (34 P2P, 0 P2M in ground truth)
    "OKAXIS",      # Axis personal (9 P2P, 0 P2M)
    "NAVIAXIS",    # Navi personal
    "WAICICI",     # WhatsApp Pay
})

# Patterns in the UPI handle (before @) that reliably indicate P2M
_P2M_HANDLE_PATTERNS = [
    ".RZP@",       # Razorpay merchants (e.g. ACKOGENERAL.RZP@AXISBANK)
    ".PAYU@",      # PayU merchants
    ".EAZYPAY@",   # ICICI EasyPay
    ".GPAY@",      # Google Pay business
    "GPAY-",       # Google Pay business (alt format)
    "PINELABS.",   # Pine Labs POS
    "POS.",        # POS terminal
    "MAB.",        # Mobile app banking POS
    "VYAPAR.",     # Vyapar invoicing platform
    "-PAYTM-",     # PayTM-routed merchant payments (31 P2M, 0 P2P in ground truth)
]

# Txn types where the category is already well-determined (skip counterparty rules)
_SKIP_COUNTERPARTY_RULES_TXN_TYPES = frozenset({
    TxnType.LOAN_INSURANCE_PAYMENT,
    TxnType.CARD_EXPENSE,
    TxnType.CARD_PAYMENT,
    TxnType.INCOME_SALARY,
    TxnType.EXPENSE_OTHER,
})

# Salary identifiers (employer payroll platforms)
_SALARY_INDICATORS = ["TIDEPLATFO", "PAYROLL"]

# Patterns that reliably identify HDFC CC bill payments
_CARD_PAYMENT_RE = re.compile(r"IB BILLPAY DR", re.IGNORECASE)

# Patterns for rent / standing instruction expenses
_RENT_RE = re.compile(r"STERLING.*RENT|NET BANKING SI.*RENT", re.IGNORECASE)

# ── ACH dividend company name canonicalisation ────────────────────────────
# ACH/COMPANY_NAME/REF — the raw company name from the description is
# title-cased by default, except for known acronyms which we preserve.
_ACH_COMPANY_TITLES: dict[str, str] = {
    "IOCL": "IOCL",                              # Indian Oil Corporation Ltd
    "NTPC": "NTPC",
    "BPCL": "BPCL",
    "ONGC": "ONGC",
    "SBI": "SBI",
    "IRCTC": "IRCTC",
    "VEDANTA LIMITED": "Vedanta Limited",
    "APOLLO TYRES LIMITED": "Apollo Tyres Limited",
    "INFOSYS LIMITED": "Infosys Limited",
    "TATA CONSULTANCY SERVICES": "Tata Consultancy Services",
}

# TxnTypes representing asset-market operations — all get NSE as counterparty
_ASSET_MARKET_TXN_TYPES = frozenset({
    TxnType.EQUITY_PURCHASE,
    TxnType.EQUITY_SALE,
    TxnType.MF_PURCHASE,
    TxnType.MF_SALE,
})

# ═══════════════════════════════════════════════════════════════════════════
# Credit card merchant classification
# ═══════════════════════════════════════════════════════════════════════════

# Swiggy ecosystem keywords — if ANY of these appear in the description,
# the transaction is routed to the Swiggy sub-classifier.
_SWIGGY_INDICATORS = ("SWIGGY", "INSTAMART", "DINEOUT", "BUNDL TECHNOLOGIES")

# Recurring CC merchants (keyword, counterparty, category).
# First match wins — put more specific patterns before general ones.
_CC_MERCHANT_RULES: list[tuple[str, str, CounterpartyCategory]] = [
    # ── Subscriptions & SaaS ─────────────────────────────────────────
    ("OPENAI", "OpenAI", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("CHATGPT", "OpenAI", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("CURSOR", "Cursor IDE", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("ELEVENLABS", "ElevenLabs", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("DIGITALOCEAN", "Digital Ocean", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("UIZARD", "Uizard", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("CANVA", "Canva", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("RAILWAY SAN FRANCISC", "Railway", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("MUTV", "MUTV", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("WISPR", "Wispr Flow", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("LINKEDIN", "LinkedIn", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("RELIANCEJIO", "Reliance Jio", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    ("PROXYCURL", "Proxycurl", CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS),
    # ── Travel & Stay ────────────────────────────────────────────────
    ("INDIGO AIRLINE", "Indigo", CounterpartyCategory.TRAVEL_STAY),
    ("MAKEMYTRIP", "MakeMyTrip", CounterpartyCategory.TRAVEL_STAY),
    ("MAKE MY TRIP", "MakeMyTrip", CounterpartyCategory.TRAVEL_STAY),
    ("M-MAKEMYTRIP", "MakeMyTrip", CounterpartyCategory.TRAVEL_STAY),
    ("REDBUS", "Redbus", CounterpartyCategory.TRAVEL_STAY),
    ("AGODA", "Agoda", CounterpartyCategory.TRAVEL_STAY),
    ("GOIBIBO", "Ibibo Group", CounterpartyCategory.TRAVEL_STAY),
    ("IBIBO GROUP", "Ibibo Group", CounterpartyCategory.TRAVEL_STAY),
    ("INDIAN HOTELS", "Taj", CounterpartyCategory.TRAVEL_STAY),
    # ── Utilities & Internet ─────────────────────────────────────────
    ("GPAY UTILITIES", "GPay Utilities", CounterpartyCategory.UTILITIES_INTERNET),
    ("PAYTM PAYMENTS UTILIT", "ACT Internet", CounterpartyCategory.UTILITIES_INTERNET),
    # ── Food & Dining ────────────────────────────────────────────────
    ("THIRD WAVE COFFEE", "Third Wave Coffee", CounterpartyCategory.FOOD_DINING),
    ("TW COFFEE", "Third Wave Coffee", CounterpartyCategory.FOOD_DINING),
    ("HUBER AND HOLLY", "Huber and Holly", CounterpartyCategory.FOOD_DINING),
    ("HIMALAYAN CULINARY", "Bao Bengaluru", CounterpartyCategory.FOOD_DINING),
    ("MC DONALDS", "McDonalds", CounterpartyCategory.FOOD_DINING),
    ("DOMINOS", "Dominos", CounterpartyCategory.FOOD_DINING),
    ("HMS HOST", "Food at Airport", CounterpartyCategory.FOOD_DINING),
    ("MSW*TRAVEL FOOD", "Food at Airport", CounterpartyCategory.FOOD_DINING),
    ("GMR HOSPITALITY", "Food at Airport", CounterpartyCategory.FOOD_DINING),
    ("PIZZA 4 PS", "Pizza 4Ps", CounterpartyCategory.FOOD_DINING),
    ("NAGARJUNA", "Nagarjuna", CounterpartyCategory.FOOD_DINING),
    ("LEON GRILL", "Leon Grill", CounterpartyCategory.FOOD_DINING),
    ("CALIFORNIA BURRITO", "California Burrito", CounterpartyCategory.FOOD_DINING),
    ("TATA STARBUCKS", "Tata Starbucks", CounterpartyCategory.FOOD_DINING),
    ("LAVONNE", "Lavonne", CounterpartyCategory.FOOD_DINING),
    # ── Shopping & E-commerce ────────────────────────────────────────
    ("MICROSOFTRS", "Microsoft", CounterpartyCategory.SHOPPING_ECOMMERCE),
    ("IND*MICROSOFT", "Microsoft", CounterpartyCategory.SHOPPING_ECOMMERCE),
    ("AMAZON SELLER SERVICES", "Amazon", CounterpartyCategory.SHOPPING_ECOMMERCE),
    ("BOOKMYSHOW", "Book My Show", CounterpartyCategory.SHOPPING_ECOMMERCE),
    ("CROSSWORD", "Crossword", CounterpartyCategory.SHOPPING_ECOMMERCE),
    ("PLAYSTATIONNETWORK", "Playstation Network", CounterpartyCategory.SHOPPING_ECOMMERCE),
    ("UNIQLO", "Uniqlo", CounterpartyCategory.SHOPPING_ECOMMERCE),
    # ── Personal Grooming ────────────────────────────────────────────
    ("ENRICH", "Enrich", CounterpartyCategory.PERSONAL_GROOMING),
    ("TATTVA SPA", "Tattva Spa", CounterpartyCategory.PERSONAL_GROOMING),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Spend-category mapping constants
# ---------------------------------------------------------------------------

# CounterpartyCategory values that map deterministically to NEED.
# These are essential living expenses that the user can't easily cut.
_NEED_CATEGORIES: frozenset[CounterpartyCategory] = frozenset({
    CounterpartyCategory.RENT_HOUSING,
    CounterpartyCategory.UTILITIES_INTERNET,
    CounterpartyCategory.HEALTHCARE_PHARMACY,
    CounterpartyCategory.TRANSPORT_FUEL,
    CounterpartyCategory.FINANCIAL_SERVICES,    # insurance, banking fees
    CounterpartyCategory.FEES_CHARGES_INTEREST,
})

# CounterpartyCategory values that map deterministically to WANT.
# Discretionary spending — lifestyle choices the user controls.
# Note: CounterpartyCategory.SWIGGY is intentionally excluded here — it needs
# sub-brand awareness (Instamart = NEED, Food/Dineout = WANT) handled separately.
_WANT_CATEGORIES: frozenset[CounterpartyCategory] = frozenset({
    CounterpartyCategory.FOOD_DINING,
    CounterpartyCategory.ENTERTAINMENT_EVENTS,
    CounterpartyCategory.SHOPPING_ECOMMERCE,
    CounterpartyCategory.TRAVEL_STAY,
    CounterpartyCategory.MOBILE_OTT_SUBSCRIPTIONS,
    CounterpartyCategory.PERSONAL_GROOMING,
    CounterpartyCategory.GIFTS_PERSONAL_TRANSFERS,
    # Note: FRIENDS_FAMILY is intentionally excluded — family/friend transfers
    # are internal flows, not standard discretionary spending. Leave NULL so the
    # user can manually tag them if desired.
})

# TxnType values that represent capital deployed into markets → INVESTMENT.
_INVESTMENT_TXN_TYPES: frozenset[TxnType] = frozenset({
    TxnType.EQUITY_PURCHASE,
    TxnType.MF_PURCHASE,
})

# TxnType values that skip spend_category entirely (income or pass-through payments).
_NO_SPEND_CATEGORY_TXN_TYPES: frozenset[TxnType] = frozenset({
    TxnType.INCOME_SALARY,
    TxnType.INCOME_OTHER,
    TxnType.INCOME_DIVIDEND,
    TxnType.CARD_PAYMENT,    # paying the CC bill — not a real spend, just a settlement
    TxnType.EQUITY_SALE,     # proceeds from selling investments = inflow
    TxnType.MF_SALE,
})


def classify_rules(txns: list[CanonicalTransaction]) -> list[CanonicalTransaction]:
    """Apply deterministic rules to a list of canonical transactions.

    Mutates the transactions in-place (sets channel, txn_type, upi_type,
    and — for known persons / heuristic matches — counterparty,
    counterparty_category, and spend_category) and returns the same list
    for chaining convenience.
    """
    for txn in txns:
        _classify_channel(txn)
        _classify_txn_type(txn)
        _classify_upi_type(txn)
        _classify_txn_type_from_upi(txn)   # P2M → UPI_EXPENSE (runs after upi_type is set)
        _classify_counterparty_category(txn)
        _classify_spend_category(txn)       # NEED/WANT/INVESTMENT (runs last)
    return txns


# ---------------------------------------------------------------------------
# Spend-category classification
# ---------------------------------------------------------------------------

def _classify_spend_category(txn: CanonicalTransaction) -> None:
    """Set spend_category deterministically where possible.

    Priority chain:
      1. INFLOW transactions → skip (income has no spend category)
      2. txn_type passes → INVESTMENT (markets/self), skip (CARD_PAYMENT, income)
      3. LOAN_INSURANCE_PAYMENT → NEED
      4. counterparty_category map → NEED, WANT, or INVESTMENT
      5. FRIENDS_FAMILY → leave None (user must manually tag)
      6. Otherwise → leave None for the LLM to fill

    Only runs after _classify_counterparty_category(), so counterparty_category
    may already be set by rules.  For transactions where counterparty_category
    is still None (to be filled by LLM), spend_category also stays None — the
    LLM prompt includes spend_category as a field to fill when requested.
    """
    # Income transactions don't have a spend category
    if txn.direction == Direction.INFLOW:
        return

    # txn_type-based shortcuts (highest confidence)
    if txn.txn_type in _NO_SPEND_CATEGORY_TXN_TYPES:
        return  # CARD_PAYMENT, income, investment sales — leave None

    if txn.txn_type in _INVESTMENT_TXN_TYPES:
        txn.spend_category = SpendCategory.INVESTMENT
        return

    if txn.txn_type == TxnType.SELF_TRANSFER:
        # Moving money to own savings / FD / wallet = capital being parked = INVESTMENT
        # (SAVING was removed — both "park money" and "deploy to markets" are INVESTMENT)
        txn.spend_category = SpendCategory.INVESTMENT
        return

    if txn.txn_type == TxnType.LOAN_INSURANCE_PAYMENT:
        # Loan repayments and insurance premiums are essential obligations
        txn.spend_category = SpendCategory.NEED
        return

    # Asset Markets outflow (e.g. counterparty_category set by broker rules)
    if txn.counterparty_category == CounterpartyCategory.ASSET_MARKETS:
        txn.spend_category = SpendCategory.INVESTMENT
        return

    # Salary & Income category (occasionally OUTFLOW from rules, e.g. reverse salary)
    if txn.counterparty_category == CounterpartyCategory.SALARY_INCOME:
        return  # ambiguous — leave for LLM

    # Self Transfer category set by counterparty rules → treat as INVESTMENT
    # (same logic as SELF_TRANSFER txn_type above)
    if txn.counterparty_category == CounterpartyCategory.SELF_TRANSFER:
        txn.spend_category = SpendCategory.INVESTMENT
        return

    # Counterparty-category-based rules (only if category was already set by rules)
    if txn.counterparty_category in _NEED_CATEGORIES:
        txn.spend_category = SpendCategory.NEED
        return

    if txn.counterparty_category in _WANT_CATEGORIES:
        txn.spend_category = SpendCategory.WANT
        return

    # Swiggy needs sub-brand awareness — the three sub-brands have different natures:
    #   Swiggy Instamart → grocery delivery → NEED (essential household items)
    #   Swiggy Food      → meal delivery    → WANT (discretionary)
    #   Swiggy Dineout   → restaurant booking → WANT (discretionary)
    #   Swiggy (ambiguous, rules couldn't pin down the sub-brand) → WANT (default)
    # The counterparty field is set by _classify_swiggy_sub() before we get here,
    # so we can trust it when it says "Swiggy Instamart".
    if txn.counterparty_category == CounterpartyCategory.SWIGGY:
        if txn.counterparty == "Swiggy Instamart":
            txn.spend_category = SpendCategory.NEED
        else:
            # Food, Dineout, or ambiguous "Swiggy" — all discretionary
            txn.spend_category = SpendCategory.WANT
        return

    # Miscellaneous and unknown categories → let LLM decide


# ---------------------------------------------------------------------------
# Channel classification  (very high confidence — narration prefixes)
# ---------------------------------------------------------------------------

def _classify_channel(txn: CanonicalTransaction) -> None:
    desc = txn.raw_description.upper()

    # ── Credit card account detection ────────────────────────────────────
    # CC account IDs are "HDFC_CC_1905" and "HDFC_CC_5778".  Check this
    # FIRST so CC transactions don't accidentally match UPI/BANK prefixes
    # that can appear in merchant names (e.g. "NEFT" in a description).
    if txn.account_id.startswith("HDFC_CC_"):
        txn.channel = Channel.CARD
        return

    # UPI-LITE must be checked before generic UPI
    if desc.startswith("UPI-LITE"):
        txn.channel = Channel.UPI_LITE
    elif desc.startswith("UPI"):
        txn.channel = Channel.UPI
    elif any(kw in desc for kw in ("NEFT ", "IMPS-", "ACH ", "RDA ", "IB BILLPAY")):
        txn.channel = Channel.BANK
    # UPI reversals (REV-UPI-...)
    elif desc.startswith("REV-UPI"):
        txn.channel = Channel.UPI
    # Third-party transfers (TPT) — bank-initiated rent payments etc.
    elif "-TPT-" in desc:
        txn.channel = Channel.BANK
    # Catch-all for remaining BANK-like patterns (standing instructions,
    # interest, processing fees, etc.)
    elif any(kw in desc for kw in (
        "NET BANKING SI",
        "INTEREST PAID",
        "PROCESSING FEE",
        "SI FAIL",
    )):
        txn.channel = Channel.BANK

    # ── ICICI-specific channel patterns ──────────────────────────────────
    # ICICI uses different prefixes from HDFC for the same underlying rails.
    #
    # IMPORTANT: Broker-originated NEFT inflows (Quant MF redemptions, NSDL
    # payouts) MUST be checked before the generic "NEFT-" branch below,
    # otherwise the NEFT- prefix match fires first and marks them as BANK.
    elif any(kw in desc for kw in ("QUANT MUTUAL FUND", "NATIONAL SECURITIES DEPOSITORY LTD")):
        txn.channel = Channel.BROKER
    elif any(desc.startswith(pfx) for pfx in (
        "MMT/IMPS/",   # ICICI's IMPS format (HDFC uses "IMPS-")
        "BIL/NEFT/",   # ICICI's NEFT bill-pay / third-party NEFT
        "BIL/INFT/",   # Internal ICICI fund transfer
        "INF/INFT/",   # Linked account transfer
        "NEFT-",       # Standard NEFT (also used by ICICI)
        "TO PPF",      # PPF contribution
        "DPCHG",       # Depository charges shorthand
    )):
        txn.channel = Channel.BANK
    elif desc.startswith("ACH/"):
        # ICICI ACH/ inflows are dividend credits from companies → routed via
        # ICICI Direct (broker).  ACH/ outflows would be loan/insurance debits
        # (none observed in this dataset, but handled correctly as BANK).
        # HDFC savings uses "ACH " (with space) which is a separate branch above.
        txn.channel = Channel.BROKER if txn.direction == Direction.INFLOW else Channel.BANK
    elif "INT.PD:" in desc:
        # Interest on savings account: "{icici_account_no}:Int.Pd:DD-MM-YYYY to ..."
        txn.channel = Channel.BANK
    elif desc.startswith("EBA/"):
        # All ICICI Direct (broker) transactions use the EBA/ prefix
        txn.channel = Channel.BROKER


# ---------------------------------------------------------------------------
# Transaction type classification
# ---------------------------------------------------------------------------

def _classify_txn_type(txn: CanonicalTransaction) -> None:
    desc = txn.raw_description
    desc_upper = desc.upper()

    # --- UPI-LITE is always a self-transfer (topping up the LITE wallet) ---
    if txn.channel == Channel.UPI_LITE:
        txn.txn_type = TxnType.SELF_TRANSFER
        return

    # ── HDFC Credit Card transactions ────────────────────────────────────
    if txn.channel == Channel.CARD:
        _classify_txn_type_card(txn, desc, desc_upper)
        return

    # ── ICICI Direct (broker) transactions ───────────────────────────────
    if txn.channel == Channel.BROKER:
        _classify_txn_type_broker(txn, desc_upper)
        return

    # --- ACH debits → loan / insurance payment ---
    # Handles both HDFC "ACH " and ICICI "ACH/" prefixes.
    if desc_upper.startswith("ACH ") or desc_upper.startswith("ACH/"):
        txn.txn_type = TxnType.LOAN_INSURANCE_PAYMENT
        return

    # --- Credit card bill payments ---
    if _CARD_PAYMENT_RE.search(desc):
        txn.txn_type = TxnType.CARD_PAYMENT
        return

    # --- Salary (inflow from known payroll platforms) ---
    if txn.direction == Direction.INFLOW and any(
        kw in desc_upper for kw in _SALARY_INDICATORS
    ):
        txn.txn_type = TxnType.INCOME_SALARY
        return

    # --- Interest credited by the bank ---
    # HDFC narration "INTEREST PAID TILL …" means interest paid *to the customer*,
    # so the direction is INFLOW and the correct type is INCOME_OTHER (not EXPENSE).
    # "CREDIT INTEREST CAPITALISED" is the same (quarterly interest credit).
    if "INTEREST PAID" in desc_upper or "CREDIT INTEREST" in desc_upper:
        txn.txn_type = TxnType.INCOME_OTHER
        return

    # --- Processing fees ---
    if "PROCESSING FEE" in desc_upper:
        txn.txn_type = TxnType.EXPENSE_OTHER
        return

    # --- Standing instruction failures (bank noise, not a real expense) ---
    if "SI FAIL" in desc_upper:
        txn.txn_type = TxnType.EXPENSE_OTHER
        return

    # --- Rent via standing instruction / net banking ---
    if _RENT_RE.search(desc):
        txn.txn_type = TxnType.EXPENSE_OTHER
        return

    # ── ICICI-specific patterns for BANK channel ─────────────────────────
    if txn.channel == Channel.BANK:
        icici_result = _classify_txn_type_icici_bank(txn, desc, desc_upper)
        if icici_result:
            return  # ICICI rule fired, txn_type already set

    # --- Self-transfer detection (own name / aliases in narration) ---
    # For NEFT/IMPS with own name or aliases, and for family transfers
    # that are classified as SELF_TRANSFER in the ground truth.
    if txn.channel == Channel.BANK:
        if _is_self_transfer(desc_upper):
            txn.txn_type = TxnType.SELF_TRANSFER
            return
        # Inflows from RDA (remittance) → INCOME_OTHER
        if desc_upper.startswith("RDA "):
            txn.txn_type = TxnType.INCOME_OTHER
            return

    # --- UPI with self/family indicators ---
    if txn.channel == Channel.UPI:
        if _is_self_transfer(desc_upper):
            txn.txn_type = TxnType.SELF_TRANSFER
            return
        # Family UPI transfers are SELF_TRANSFER in the ground truth
        # (counterparty_category is set to "Friends and Family" later).
        upi_name = _extract_upi_name(desc)
        if upi_name and _check_against_list(upi_name, _FAMILY_NAMES):
            txn.txn_type = TxnType.SELF_TRANSFER
            return
        # UPI distinction between EXPENSE and TRANSFER is hard with rules
        # alone (requires knowing if counterparty is a merchant or person).
        # Leave as None for the LLM to handle.


# ---------------------------------------------------------------------------
# Credit card txn-type helper  (called when channel == CARD)
# ---------------------------------------------------------------------------

def _classify_txn_type_card(
    txn: CanonicalTransaction, desc: str, desc_upper: str
) -> None:
    """Set txn_type for HDFC credit card transactions.

    The CARD channel means every debit is a purchase (CARD_EXPENSE) unless a
    more specific pattern matches first (CC bill payment, EMI, fee, cashback).
    """
    # ── Inflows ──────────────────────────────────────────────────────────
    if txn.direction == Direction.INFLOW:
        # CC bill payment credited back (payment reversal or overpayment)
        if "CREDIT CARD PAYMENT" in desc_upper or "NETBANKING TRANSFER" in desc_upper:
            txn.txn_type = TxnType.CARD_PAYMENT
            return
        # Cashback credited to the card
        if "CASHBACK" in desc_upper or "REINSTATED CASHBACK" in desc_upper:
            txn.txn_type = TxnType.INCOME_OTHER
            return
        # Any other inflow (reward redemption, refund) → INCOME_OTHER
        txn.txn_type = TxnType.INCOME_OTHER
        return

    # ── Outflows ─────────────────────────────────────────────────────────
    # EMI payments (on-us or off-us) on the credit card
    if "OFFUS EMI" in desc_upper or "MER EMI" in desc_upper:
        txn.txn_type = TxnType.LOAN_INSURANCE_PAYMENT
        return

    # IGST / GST fees charged on the statement date
    if desc_upper.startswith("IGST-"):
        txn.txn_type = TxnType.EXPENSE_OTHER
        return

    # HDFC SmartBuy portal hold/refund (tiny ₹2 debit or credit from CT HOTEL / CT FLIGHT)
    # These are not real purchases — they are authorization holds or SmartBuy portal credits.
    if "VIA SMARTBUY" in desc_upper:
        txn.txn_type = TxnType.EXPENSE_OTHER
        return

    # Bank fees (foreign currency markup, DCC fee)
    if any(kw in desc_upper for kw in (
        "CONSOLIDATED FCY",
        "1% ON ALL DCC",
    )):
        txn.txn_type = TxnType.EXPENSE_OTHER
        return

    # Reward-points redemption processing fee — treated as an EMI-adjacent fee
    # rather than a generic bank expense (consistent with user benchmark labels)
    if "REDEMPTION PROC FEE" in desc_upper:
        txn.txn_type = TxnType.LOAN_INSURANCE_PAYMENT
        return

    # All other outflows are individual card purchases
    txn.txn_type = TxnType.CARD_EXPENSE


# ---------------------------------------------------------------------------
# Broker txn-type helper  (called when channel == BROKER, i.e. EBA/ prefix)
# ---------------------------------------------------------------------------

def _classify_txn_type_broker(
    txn: CanonicalTransaction, desc_upper: str
) -> None:
    """Set txn_type for ICICI Direct (EBA/) and ACH dividend transactions.

    Direction is crucial here:
      - EQ Trade / NSE M / EQ Margin OUTFLOW → buying equities (EQUITY_PURCHASE)
      - EQ Trade / NSE M / EQ Margin INFLOW  → selling equities (EQUITY_SALE)
      - eATM is always an inflow (instant redemption back to bank) → EQUITY_SALE
      - MFP (Mutual Fund Purchase) → always an outflow → MF_PURCHASE
      - ACH/ inflows → dividend credited by a company → INCOME_DIVIDEND
    """
    # Dividend credited via ACH (set to BROKER channel for inflows by _classify_channel)
    if desc_upper.startswith("ACH/"):
        txn.txn_type = TxnType.INCOME_DIVIDEND
        return

    # MF redemption proceeds via NEFT from the fund house (e.g. Quant Mutual Fund)
    if "QUANT MUTUAL FUND" in desc_upper and txn.direction == Direction.INFLOW:
        txn.txn_type = TxnType.MF_SALE
        return

    # NSDL NEFT inflow: proceeds from equity/bond sales distributed via NSDL
    if "NATIONAL SECURITIES DEPOSITORY LTD" in desc_upper and txn.direction == Direction.INFLOW:
        txn.txn_type = TxnType.EQUITY_SALE
        return

    # EQ Trade, NSE M settlement, EQ Margin: direction decides buy vs sell
    if any(kw in desc_upper for kw in ("EBA/EQ TRADE", "EBA/NSE M", "EBA/EQ MARGIN")):
        txn.txn_type = (
            TxnType.EQUITY_SALE if txn.direction == Direction.INFLOW
            else TxnType.EQUITY_PURCHASE
        )
        return

    # eATM = instant equity/MF redemption — money flows back to bank (always inflow)
    if "EBA/EATM" in desc_upper:
        txn.txn_type = TxnType.EQUITY_SALE
        return

    # MFP = Mutual Fund Purchase (EBA/MFP-...) — always an outflow from bank
    if "EBA/MFP" in desc_upper:
        txn.txn_type = TxnType.MF_PURCHASE
        return

    # EBA/PUR_* = "Purchase" via ICICI Direct — still a broker buy order
    # (e.g. EBA/PUR_PRIME900 is a purchase of a specific scrip/series,
    # NOT an Amazon Prime subscription despite the name similarity)
    if "EBA/PUR_" in desc_upper:
        txn.txn_type = TxnType.EQUITY_PURCHASE
        return

    # NSDL/CDSL annual depository charges billed via ICICI Direct
    if "EBA/DEPOSITORY" in desc_upper:
        txn.txn_type = TxnType.EXPENSE_OTHER
        return

    # Leave remaining EBA/ transactions for the LLM


# ---------------------------------------------------------------------------
# ICICI savings bank-channel txn-type helper
# ---------------------------------------------------------------------------

def _classify_txn_type_icici_bank(
    txn: CanonicalTransaction, desc: str, desc_upper: str
) -> bool:
    """Apply ICICI-specific BANK-channel rules.  Returns True if a rule fired."""

    # ── Interest on savings account ───────────────────────────────────────
    # Format: "{icici_account_no}:Int.Pd:DD-MM-YYYY to DD-MM-YYYY"
    if "INT.PD:" in desc_upper:
        txn.txn_type = TxnType.INCOME_OTHER
        return True

    # ── Depository charges (short form in some months) ────────────────────
    if desc_upper.startswith("DPCHG"):
        txn.txn_type = TxnType.EXPENSE_OTHER
        return True

    # ── PPF contribution (own account) ────────────────────────────────────
    if desc_upper.startswith("TO PPF"):
        txn.txn_type = TxnType.SELF_TRANSFER
        return True

    # Quant MF and NSDL NEFT inflows are re-routed to BROKER channel by
    # _classify_channel and handled upstream by _classify_txn_type_broker.

    # ── Bike EMI "payment" from ICICI is actually a self-transfer ─────────
    # The user transfers money from ICICI → HDFC (where the EMI is debited).
    # From ICICI's perspective the transaction is simply moving money to one's
    # own HDFC account — the actual loan debit happens at HDFC level.
    if "BIKEEMI" in desc_upper:
        txn.txn_type = TxnType.SELF_TRANSFER
        return True

    # ── BIL/INFT (Internal Fund Transfer) to a known person ──────────────
    # If the recipient is someone we recognise (family/friends), it is a
    # genuine bank transfer to that person, NOT a self-transfer.
    # Unknown recipients are left for the LLM.
    if "BIL/INFT/" in desc_upper:
        all_known = _FAMILY_NAMES + _FRIENDS_NAMES + _ACQUAINTANCES_NAMES
        if "K ADI LAKSHMI" in desc_upper or _find_person_in_desc(desc, all_known):
            txn.txn_type = TxnType.BANK_TRANSFER
            return True

    # ── Self / family transfers ───────────────────────────────────────────
    # "SelfFund" label in IMPS/NEFT narration
    if "SELFFUND" in desc_upper:
        txn.txn_type = TxnType.SELF_TRANSFER
        return True

    # Linked account transfer (INF/INFT/) — always own account
    if desc_upper.startswith("INF/INFT/"):
        txn.txn_type = TxnType.SELF_TRANSFER
        return True

    # Family transfers in ICICI narrations (same ground-truth as HDFC)
    if "/FAMILY/" in desc_upper:
        txn.txn_type = TxnType.SELF_TRANSFER
        return True

    return False


def _is_self_transfer(desc_upper: str) -> bool:
    """Check if narration indicates a transfer between own accounts."""
    return any(indicator in desc_upper for indicator in _SELF_INDICATORS)


# ---------------------------------------------------------------------------
# UPI type classification
# ---------------------------------------------------------------------------

def _classify_upi_type(txn: CanonicalTransaction) -> None:
    if txn.channel == Channel.UPI_LITE:
        txn.upi_type = UPIType.LITE_SELF_FUND
    elif txn.channel == Channel.UPI:
        detected = _detect_upi_p2p_or_p2m(txn.raw_description)
        if detected is not None:
            txn.upi_type = detected
    elif txn.channel in (Channel.BANK, Channel.CARD, Channel.BROKER):
        txn.upi_type = UPIType.NA


def _classify_txn_type_from_upi(txn: CanonicalTransaction) -> None:
    """Use UPI-type to deterministically fill txn_type when still unset.

    Rules (only applied when txn_type is None):
    - P2M + OUTFLOW → UPI_EXPENSE  (paying a merchant)
    - P2M + INFLOW  → INCOME_OTHER (refund / cashback from a merchant)
    - P2P + INFLOW  → UPI_TRANSFER (friend/family sending money back)

    P2P outflows are left for the LLM — they could be UPI_TRANSFER or
    SELF_TRANSFER depending on who the recipient is.
    """
    if txn.channel != Channel.UPI or txn.txn_type is not None:
        return

    if txn.upi_type == UPIType.P2M:
        if txn.direction == Direction.INFLOW:
            txn.txn_type = TxnType.INCOME_OTHER
        else:
            txn.txn_type = TxnType.UPI_EXPENSE

    elif txn.upi_type == UPIType.P2P:
        # P2P is always a transfer to/from another person.  Self-transfers
        # are caught earlier in _classify_txn_type, so anything still None
        # here is a genuine person-to-person transfer in either direction.
        txn.txn_type = TxnType.UPI_TRANSFER


# ---------------------------------------------------------------------------
# Name matching helpers
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Uppercase and collapse whitespace for consistent comparison."""
    return " ".join(name.upper().split())


def _names_match(extracted: str, canonical: str) -> bool:
    """Check if an extracted transaction name matches a canonical name.

    Handles three scenarios:
      - Exact match (after normalisation)
      - UPI truncation (~20-char limit) via prefix matching
      - Name reordering (e.g. RDA "VENKATA VINOD KRISHNA KUPPA"
        vs canonical "KUPPA VENKATA VINOD KRISHNA") via word-set matching
    """
    e = _normalize_name(extracted)
    c = _normalize_name(canonical)

    if e == c:
        return True

    # Prefix: one string starts with the other (handles UPI truncation)
    if min(len(e), len(c)) >= 5:
        if c.startswith(e) or e.startswith(c):
            return True

    # Word-set: all words match regardless of order (needs >= 3 words to
    # reduce false positives from common first/last names)
    e_words = set(e.split())
    c_words = set(c.split())
    if len(e_words) >= 3 and len(c_words) >= 3:
        if c_words.issubset(e_words) or e_words.issubset(c_words):
            return True

    return False


def _extract_upi_name(desc: str) -> str | None:
    """Extract the person/business name from a UPI description.

    Standard format: ``UPI-<NAME>-<UPI_HANDLE>@<BANK>-<BANK_CODE>-<REF>-<NOTE>``
    The name is always the field at index 1 when splitting on ``-`` up to
    the first ``@`` sign.  Returns *None* for non-UPI or unparseable strings.
    """
    if not desc.upper().startswith("UPI-"):
        return None

    at_pos = desc.find("@")
    if at_pos == -1:
        return None

    before_at = desc[:at_pos]
    parts = before_at.split("-")
    if len(parts) < 2:
        return None

    name = parts[1].strip()
    return name if name else None


def _check_against_list(name: str, name_list: list[str]) -> str | None:
    """Return the matched canonical name if *name* matches any entry, else *None*."""
    for canonical in name_list:
        if _names_match(name, canonical):
            return canonical
    return None


def _find_person_in_desc(desc: str, name_list: list[str]) -> str | None:
    """Scan a full description (NEFT / IMPS / RDA) for any name in the list.

    Uses substring matching on the normalised description, with prefix
    fallback for truncated names and word-set matching for reordered names.

    Bank descriptions use hyphens as separators (e.g.
    ``RDA FIR INW-R25601497544-VENKATA VINOD KRISHNA KUPPA``),
    so hyphens are replaced with spaces before word-set matching.
    """
    desc_norm = _normalize_name(desc)
    # Replace hyphens with spaces so "INW-VENKATA" splits into separate words
    desc_norm_dehyphen = _normalize_name(desc.replace("-", " "))

    for canonical in name_list:
        c_norm = _normalize_name(canonical)

        # Direct substring
        if c_norm in desc_norm:
            return canonical

        # Truncated prefix (try removing 1-4 trailing chars, min 10 chars)
        if len(c_norm) > 15:
            for end in range(len(c_norm) - 1, max(9, len(c_norm) - 5), -1):
                if c_norm[:end] in desc_norm:
                    return canonical

        # Word-set for reordered names (e.g. KUPPA VENKATA … vs VENKATA … KUPPA)
        c_words = set(c_norm.split())
        if len(c_words) >= 3:
            desc_words = set(desc_norm_dehyphen.split())
            if c_words.issubset(desc_words):
                return canonical

    return None


def _looks_like_merchant_name(name: str) -> bool:
    """Heuristic: does a UPI name look like a business rather than a person?

    Truncation-safe: also matches when a word in *name* is a prefix of a
    keyword (or vice-versa) with a minimum overlap of 5 characters.
    This catches UPI-truncated names like "APOLLO PHARMAC" → "PHARMACY".
    """
    upper = name.upper()

    # Fast path: exact keyword substring
    if any(kw in upper for kw in _MERCHANT_NAME_KEYWORDS):
        return True

    # Truncation-safe: compare each word against each keyword via prefix
    words = upper.split()
    for word in words:
        if len(word) < 5:
            continue
        for kw in _MERCHANT_NAME_KEYWORDS:
            if len(kw) < 5:
                continue
            if word.startswith(kw) or kw.startswith(word):
                return True

    return False


def _extract_upi_bank_suffix(desc: str) -> str | None:
    """Extract the bank/PSP suffix from a UPI handle (the part after @).

    For ``UPI-NAME-HANDLE@SUFFIX-BANKCODE-REF-NOTE`` returns ``SUFFIX``.
    """
    at_pos = desc.find("@")
    if at_pos == -1:
        return None
    after_at = desc[at_pos + 1:]
    return after_at.split("-")[0].upper() if after_at else None


def _detect_upi_p2p_or_p2m(desc: str) -> UPIType | None:
    """Detect P2P or P2M from the UPI handle pattern.

    Uses only high-confidence patterns that are exclusive to one category
    in the ground truth.  Returns *None* when the handle is ambiguous
    (e.g. @PTYS, @PAYTM, @YBL) and should be left for the LLM.
    """
    suffix = _extract_upi_bank_suffix(desc)
    if suffix is None:
        return None

    if suffix in _P2M_BANK_SUFFIXES:
        return UPIType.P2M
    if suffix in _P2P_BANK_SUFFIXES:
        return UPIType.P2P

    # Check handle-level patterns (e.g. ".RZP@", ".PAYU@")
    desc_upper = desc.upper()
    for pattern in _P2M_HANDLE_PATTERNS:
        if pattern in desc_upper:
            return UPIType.P2M

    return None


# ---------------------------------------------------------------------------
# Swiggy sub-brand classifier
# ---------------------------------------------------------------------------

def _classify_swiggy_sub(desc_upper: str) -> str | None:
    """Identify which Swiggy sub-brand a CC transaction belongs to.

    Returns one of: "Swiggy Food", "Swiggy Instamart", "Swiggy Dineout",
    or "Swiggy" (generic/ambiguous).  Returns None if not a Swiggy txn.

    Classification hierarchy:
      1. Explicit sub-brand keyword in description (INSTAMART, DINEOUT, SWIGGY FOOD)
      2. Payment gateway prefix pattern (CAS* = Dineout, PPSL* = depends, etc.)
      3. Fallback: ambiguous → "Swiggy"
    """
    if not any(kw in desc_upper for kw in _SWIGGY_INDICATORS):
        return None

    # ── Explicit sub-brand keywords (highest confidence) ──────────────
    if "INSTAMART" in desc_upper:
        return "Swiggy Instamart"
    if "DINEOUT" in desc_upper:
        return "Swiggy Dineout"
    if "SWIGGY FOOD" in desc_upper:
        return "Swiggy Food"

    # ── Payment gateway prefix patterns ───────────────────────────────
    # CAS*Swiggy (Cashfree routed) → Dineout bookings
    if desc_upper.startswith("CAS*SWIGGY"):
        return "Swiggy Dineout"
    # PPSL*Swiggy (PayU PPSL) → "LIMITED" suffix = Food, otherwise Dineout
    if desc_upper.startswith("PPSL*SWIGGY"):
        return "Swiggy Food" if "LIMITED" in desc_upper else "Swiggy Dineout"
    # BUNDL TECHNOLOGIES (Swiggy's corporate name) without RAZ* gateway = Instamart
    if "BUNDL TECHNOLOGIES" in desc_upper and not desc_upper.startswith("RAZ*"):
        return "Swiggy Instamart"
    # Razorpay*/Cashfree*/ING* routed to Swiggy = Food orders
    if any(desc_upper.startswith(pfx) for pfx in (
        "RAZORPAY*SWIGGY", "CASHFREE*SWIGGY", "ING*SWIGGY",
    )):
        return "Swiggy Food"
    # PayU/PYU routed through "Swiggy Limited" (not "Swiggy Food") = Food
    if any(pfx in desc_upper for pfx in ("PAYU*SWIGGY LIMITED", "PYU*SWIGGY")):
        return "Swiggy Food"
    # Payu*Swiggy Food explicitly
    if "PAYU*SWIGGY FOOD" in desc_upper:
        return "Swiggy Food"

    # ── Ambiguous ─────────────────────────────────────────────────────
    # "SWIGGY BANGALORE", "WWW SWIGGY COM", "RAZ*BUNDL TECHNOLOGIES",
    # "Swiggy Bengaluru" — could be Food, Instamart, or Dineout.
    # User cross-referenced with the Swiggy app; for the rules classifier
    # we default to generic "Swiggy" and leave fine-grained splits for
    # ratio-based allocation in post-processing.
    return "Swiggy"


# ---------------------------------------------------------------------------
# Credit card counterparty classifier
# ---------------------------------------------------------------------------

def _classify_cc_counterparty(txn: CanonicalTransaction) -> None:
    """Set counterparty + category for all CARD channel transactions.

    Covers: cashback credits, bill payments, GST/forex fees, EMI
    principal/interest, Swiggy sub-brands, and recurring merchants.
    """
    desc_upper = txn.raw_description.upper()

    # ── 1. Inflows (cashback, bill payments, refunds) ─────────────────
    if txn.direction == Direction.INFLOW:
        if "CASHBACK" in desc_upper or "REINSTATED" in desc_upper:
            txn.counterparty = "HDFC Bank"
            txn.counterparty_category = CounterpartyCategory.MISCELLANEOUS
            return
        if "NETBANKING TRANSFER" in desc_upper or "CREDIT CARD PAYMENT" in desc_upper:
            txn.counterparty = "Sashank Sai Kuppa"
            txn.counterparty_category = CounterpartyCategory.SELF_TRANSFER
            return
        # Swiggy refund (inflow from a Swiggy entity)
        if any(kw in desc_upper for kw in _SWIGGY_INDICATORS):
            txn.counterparty = "Swiggy"
            txn.counterparty_category = CounterpartyCategory.MISCELLANEOUS
            return
        # HDFC SmartBuy portal reversal/credit (e.g. CT HOTEL VIA SMARTBUY)
        if "VIA SMARTBUY" in desc_upper:
            txn.counterparty = "HDFC Bank"
            txn.counterparty_category = CounterpartyCategory.MISCELLANEOUS
            return
        return  # other inflows → LLM

    # ── 2. EXPENSE_OTHER outflows (GST, forex markup, DCC fees, SmartBuy holds) ──
    if txn.txn_type == TxnType.EXPENSE_OTHER:
        if desc_upper.startswith("IGST"):
            txn.counterparty = "GST"
            txn.counterparty_category = CounterpartyCategory.FEES_CHARGES_INTEREST
            return
        if "CONSOLIDATED FCY" in desc_upper:
            txn.counterparty = "Forex Markup"
            txn.counterparty_category = CounterpartyCategory.FEES_CHARGES_INTEREST
            return
        if "1% ON ALL DCC" in desc_upper:
            txn.counterparty = "DCC Fee"
            txn.counterparty_category = CounterpartyCategory.FEES_CHARGES_INTEREST
            return
        if "VIA SMARTBUY" in desc_upper:
            txn.counterparty = "HDFC Bank"
            txn.counterparty_category = CounterpartyCategory.MISCELLANEOUS
            return
        return

    # ── 3. LOAN_INSURANCE_PAYMENT (EMI principal / interest / fees) ───
    if txn.txn_type == TxnType.LOAN_INSURANCE_PAYMENT:
        if "PROCNG FEE" in desc_upper or "PROCG FEE" in desc_upper:
            txn.counterparty = "EMI Processing Fees"
        elif "REDEMPTION PROC FEE" in desc_upper:
            txn.counterparty = "EMI Processing Fees"
        elif "PRIN" in desc_upper:
            txn.counterparty = "EMI Principal"
        elif "INT" in desc_upper:
            txn.counterparty = "EMI Interest"
        txn.counterparty_category = CounterpartyCategory.FEES_CHARGES_INTEREST
        return

    # ── 4. CARD_EXPENSE (merchants) ───────────────────────────────────
    if txn.txn_type == TxnType.CARD_EXPENSE:
        # 4a. Swiggy ecosystem → sub-brand classification
        swiggy = _classify_swiggy_sub(desc_upper)
        if swiggy:
            txn.counterparty = swiggy
            txn.counterparty_category = CounterpartyCategory.SWIGGY
            return
        # 4b. Recurring merchant keyword table (first match wins)
        for keyword, counterparty, category in _CC_MERCHANT_RULES:
            if keyword in desc_upper:
                txn.counterparty = counterparty
                txn.counterparty_category = category
                return
        # 4c. Unknown merchant → leave for LLM


# ---------------------------------------------------------------------------
# ACH dividend counterparty extraction
# ---------------------------------------------------------------------------

def _extract_ach_counterparty(desc: str) -> str | None:
    """Extract and canonicalise the company name from an ACH dividend credit.

    ICICI format: ``ACH/<COMPANY_NAME>/<REFERENCE_NUMBER>``
    e.g. "ACH/VEDANTA LIMITED/33981805" → "Vedanta Limited"
         "ACH/IOCL/26483633" → "IOCL"
    """
    m = re.match(r"ACH/([^/]+)/\d+", desc.strip(), re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip().upper()
    # Return the known canonical form; fall back to title-case for unknown companies
    return _ACH_COMPANY_TITLES.get(raw, raw.title())


# ---------------------------------------------------------------------------
# Counterparty & category classification
# ---------------------------------------------------------------------------

def _classify_counterparty_category(txn: CanonicalTransaction) -> None:
    """Set counterparty_category (and sometimes counterparty) deterministically.

    Priority chain:
      1. SELF_TRANSFER + family name → Friends and Family
      2. SELF_TRANSFER (not family) → Self Transfer
      3. Family / friends list → Friends and Family
      4. Acquaintances list → Gifts & Personal Transfers
      5. UPI outflow to unknown non-merchant person:
         a. Amount < ₹100 → Miscellaneous
         b. Everything else → leave for LLM (includes Uber/Ola detection)
      6. Anything else → leave for LLM
    """
    desc = txn.raw_description
    desc_upper = desc.upper()

    # ════════════════════════════════════════════════════════════════════════
    # PRE-SKIP RULES — run before the skip-list check so that certain
    # EXPENSE_OTHER / INCOME_OTHER / CARD transactions still get a
    # deterministic counterparty instead of going to the LLM.
    # ════════════════════════════════════════════════════════════════════════

    # ── Credit card: full counterparty classification ─────────────────
    if txn.channel == Channel.CARD:
        _classify_cc_counterparty(txn)
        return

    # ── Bank interest (savings account credits) ─────────────────────────
    # Covers both "INTEREST PAID TILL …" and "CREDIT INTEREST CAPITALISED".
    if "INTEREST PAID" in desc_upper or "CREDIT INTEREST" in desc_upper:
        txn.counterparty = "Bank Interest"
        txn.counterparty_category = CounterpartyCategory.FEES_CHARGES_INTEREST
        return

    # ── Sterling rent payments always go to Ashlesha Naokarkar ───────────
    if txn.txn_type == TxnType.EXPENSE_OTHER and txn.channel == Channel.BANK:
        if "STERLING" in desc_upper and "RENT" in desc_upper:
            txn.counterparty = "Ashlesha Naokarkar"
            txn.counterparty_category = CounterpartyCategory.RENT_HOUSING
            return

    # ── ICICI savings account interest ───────────────────────────────────
    # "{icici_account_no}:Int.Pd:DD-MM-YYYY to DD-MM-YYYY"
    if "INT.PD:" in desc_upper:
        txn.counterparty = "ICICI Bank"
        txn.counterparty_category = CounterpartyCategory.FEES_CHARGES_INTEREST
        return

    # ── ICICI depository / maintenance charges ────────────────────────────
    if desc_upper.startswith("DPCHG"):
        txn.counterparty = "ICICI Bank"
        txn.counterparty_category = CounterpartyCategory.FEES_CHARGES_INTEREST
        return

    # ── BIL/INFT to K Adi Lakshmi (family name truncated in ICICI) ───────
    # ICICI truncates "KUPPA ADI LAKSHMI" to "K ADI LAKSHMI" in narrations.
    # Handle this before the generic name-list search which won't match it.
    if "BIL/INFT/" in desc_upper and "K ADI LAKSHMI" in desc_upper:
        txn.counterparty = "K Adi Lakshmi"
        txn.counterparty_category = CounterpartyCategory.FRIENDS_FAMILY
        return

    # ── Asset-market transactions (equity / MF buys and sells) ───────────
    # All EBA/ trades settle against NSE; MF redemptions / purchases also
    # route through NSE infrastructure, so NSE is the canonical counterparty.
    if txn.txn_type in _ASSET_MARKET_TXN_TYPES:
        txn.counterparty = "NSE"
        txn.counterparty_category = CounterpartyCategory.ASSET_MARKETS
        return

    # ── Dividend income (ACH/ from a company) ────────────────────────────
    if txn.txn_type == TxnType.INCOME_DIVIDEND:
        txn.counterparty = _extract_ach_counterparty(desc) or "Unknown Company"
        txn.counterparty_category = CounterpartyCategory.ASSET_MARKETS
        return

    # ════════════════════════════════════════════════════════════════════════
    # Transaction types that already imply a specific category — the LLM
    # handles their counterparty well and adding rules here would be fragile.
    # ════════════════════════════════════════════════════════════════════════
    if txn.txn_type in _SKIP_COUNTERPARTY_RULES_TXN_TYPES:
        return

    # ── SELF_TRANSFER: distinguish "self" from "family" ──────────────────
    if txn.txn_type == TxnType.SELF_TRANSFER:
        if txn.channel == Channel.UPI:
            upi_name = _extract_upi_name(desc)
            if upi_name and _check_against_list(upi_name, _FAMILY_NAMES):
                txn.counterparty_category = CounterpartyCategory.FRIENDS_FAMILY
                return
        elif _find_person_in_desc(desc, _FAMILY_NAMES):
            txn.counterparty_category = CounterpartyCategory.FRIENDS_FAMILY
            return
        # All non-family self-transfers: use the user's own name
        txn.counterparty = "Sashank Sai Kuppa"
        txn.counterparty_category = CounterpartyCategory.SELF_TRANSFER
        return

    # ── Known merchants: deterministic counterparty + category ───────────
    # These are merchants whose UPI handle is unambiguous but whose name
    # the LLM sometimes misclassifies (inconsistent counterparty or wrong
    # category across multiple transactions).
    if txn.channel == Channel.UPI:
        upi_name_upper = (_extract_upi_name(desc) or "").upper()
        if "BBINSTANT" in upi_name_upper:
            txn.counterparty = "BB Instant"
            txn.counterparty_category = CounterpartyCategory.FOOD_DINING
            return

    # ── Named-list matching (family → friends → acquaintances) ───────────
    ordered_lists = [
        (_FAMILY_NAMES, CounterpartyCategory.FRIENDS_FAMILY),
        (_FRIENDS_NAMES, CounterpartyCategory.FRIENDS_FAMILY),
        (_ACQUAINTANCES_NAMES, CounterpartyCategory.GIFTS_PERSONAL_TRANSFERS),
    ]

    if txn.channel == Channel.UPI:
        upi_name = _extract_upi_name(desc)
        if upi_name:
            for name_list, category in ordered_lists:
                if _check_against_list(upi_name, name_list):
                    txn.counterparty_category = category
                    return
    else:
        for name_list, category in ordered_lists:
            canonical = _find_person_in_desc(desc, name_list)
            if canonical:
                txn.counterparty = canonical
                txn.counterparty_category = category
                return

    # ── UPI outflow heuristics ────────────────────────────────────────────
    if txn.channel == Channel.UPI and txn.direction == Direction.OUTFLOW:
        upi_name = _extract_upi_name(desc)
        if not upi_name:
            return

        upi_name_upper = upi_name.upper()
        upi_words = upi_name_upper.split()

        # Hotels, resorts, tour operators, travel booking sites → Travel & Stay.
        # Word-level prefix check is truncation-safe: "TRAV" matches "TRAVEL".
        if any(word.startswith(kw) for word in upi_words
               for kw in _HOTEL_TRAVEL_KEYWORDS):
            txn.counterparty_category = CounterpartyCategory.TRAVEL_STAY
            return

        # Skip further heuristics if the name looks like a merchant (truncation-safe)
        # OR the handle is a reliably-P2M bank suffix (belt and suspenders).
        is_merchant_name = _looks_like_merchant_name(upi_name)
        is_reliable_p2m = (
            _extract_upi_bank_suffix(desc) in _P2M_BANK_SUFFIXES
            or any(p in desc.upper() for p in _P2M_HANDLE_PATTERNS)
        )
        if is_merchant_name or is_reliable_p2m:
            # Recognised merchant but not a hotel/travel — let LLM classify
            return

        amount = float(txn.amount)

        # Small amounts (< ₹100) to unknown persons — too small for
        # meaningful classification; label as Miscellaneous.
        if amount < 100:
            txn.counterparty_category = CounterpartyCategory.MISCELLANEOUS
            return

        # Everything else (including possible Uber/Ola rides to personal
        # UPI handles) — let the LLM decide. The prompt includes heuristic
        # guidance for ride-hailing detection so the LLM can make an
        # informed judgement call based on amount + UPI name pattern.
