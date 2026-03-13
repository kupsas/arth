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

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_rules(txns: list[CanonicalTransaction]) -> list[CanonicalTransaction]:
    """Apply deterministic rules to a list of canonical transactions.

    Mutates the transactions in-place (sets channel, txn_type, upi_type,
    and — for known persons / heuristic matches — counterparty and
    counterparty_category) and returns the same list for chaining convenience.
    """
    for txn in txns:
        _classify_channel(txn)
        _classify_txn_type(txn)
        _classify_upi_type(txn)
        _classify_txn_type_from_upi(txn)   # P2M → UPI_EXPENSE (runs after upi_type is set)
        _classify_counterparty_category(txn)
    return txns


# ---------------------------------------------------------------------------
# Channel classification  (very high confidence — narration prefixes)
# ---------------------------------------------------------------------------

def _classify_channel(txn: CanonicalTransaction) -> None:
    desc = txn.raw_description.upper()

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

    # --- ACH debits → loan / insurance payment ---
    if desc_upper.startswith("ACH "):
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

    # --- Interest paid by the bank ---
    if "INTEREST PAID" in desc_upper:
        txn.txn_type = TxnType.EXPENSE_OTHER
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
        if txn.direction == Direction.INFLOW:
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
         a. Amount ₹100–1 500 → Uber (Transport & Fuel)
         b. Amount > ₹1 500 → Gifts & Personal Transfers
         c. Amount < ₹100 → Miscellaneous
      6. Anything else → leave for LLM
    """
    desc = txn.raw_description

    # ── Sterling rent payments always go to Ashlesha Naokarkar ───────────
    # This rule runs BEFORE the skip list so that EXPENSE_OTHER rent txns
    # still get a named counterparty instead of the LLM guessing "Sterling Rent".
    if txn.txn_type == TxnType.EXPENSE_OTHER and txn.channel == Channel.BANK:
        desc_upper = desc.upper()
        if "STERLING" in desc_upper and "RENT" in desc_upper:
            txn.counterparty = "Ashlesha Naokarkar"
            txn.counterparty_category = CounterpartyCategory.RENT_HOUSING
            return

    # Transaction types that already imply a specific category (e.g. rent,
    # loan EMI, salary) — the LLM handles their categories well.
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

        # Uber/Ola ride: unknown non-merchant person, amount ₹100–₹1 500.
        if 100 <= amount <= 1500:
            txn.counterparty = "Uber"
            txn.counterparty_category = CounterpartyCategory.TRANSPORT_FUEL
            return

        # Small amounts (< ₹100) to unknown persons
        if amount < 100:
            txn.counterparty_category = CounterpartyCategory.MISCELLANEOUS
            return

        # Large amounts to unknown non-merchant persons — let the LLM decide.
        # (Removed the blanket "Gifts & Personal Transfers" default that was
        # catching legitimate merchant purchases like EatClub, clothing stores.)
