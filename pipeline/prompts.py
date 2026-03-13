"""
LLM prompt templates for transaction classification.

These are **fresh prompts** informed by the manually-corrected GSheet ground
truth (647 rows).  The old GSheet prompts (GSheet_prompts_used.md) are kept
as structural reference only — they produced imperfect results.

Each prompt function returns a (system, user) tuple of strings.  The LLM
classifier calls these and sends them to whichever model is configured.

Prompt strategies:
  - ``batch_classify_prompt``   — single-pass: all fields at once
  - ``two_pass_fields_prompt``  — pass 1: txn_type + upi_type + counterparty
  - ``two_pass_category_prompt``— pass 2: counterparty_category from "txn_type counterparty"
"""

from __future__ import annotations

# ── Allowed values (kept in sync with models.py enums) ─────────────────────

_TXN_TYPES = """UPI_EXPENSE, UPI_TRANSFER, BANK_TRANSFER, SELF_TRANSFER,
CARD_PAYMENT, INCOME_SALARY, INCOME_OTHER, EXPENSE_OTHER,
LOAN_INSURANCE_PAYMENT"""

_UPI_TYPES = "P2P, P2M"

_CATEGORIES = """Entertainment & Events
Fees, Charges & Interest
Financial Services, Insurance & Banking
Food & Dining
Friends and Family
Gifts & Personal Transfers
Healthcare & Pharmacy
Miscellaneous
Mobile, OTT & Subscriptions
Personal Grooming
Rent & Housing
Salary & Income
Self Transfer
Shopping & E-commerce
Swiggy
Transport & Fuel
Travel & Stay
Utilities & Internet"""

# ── Shared few-shot examples ───────────────────────────────────────────────

_FEW_SHOT = """\
Example 1 — UPI P2M subscription:
  desc: UPI-SPOTIFY INDIA-SPOTIFY.BDSI@ICICI-ICIC0DC0099-500693553497-MANDATEREQUEST
  direction: OUTFLOW | amount: 119 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=Spotify | counterparty_category=Mobile, OTT & Subscriptions

Example 2 — UPI P2M food delivery:
  desc: UPI-SWIGGY LIMITED-SWIGGYINSTAMART1ONLINE.GPAY@OKPAYAXIS-UTIB0000553
  direction: OUTFLOW | amount: 339 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=Swiggy | counterparty_category=Swiggy

Example 3 — UPI P2M cafe:
  desc: UPI-THIRD WAVE COFFEE-THIRDWAVECOFFEE.42605934@HDFCBANK-HDFC0000001-504311834904-UPI
  direction: OUTFLOW | amount: 445 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=Third Wave Coffee | counterparty_category=Food & Dining

Example 4 — UPI P2M pharmacy:
  desc: UPI-APOLLO PHARMACY-APOLLOPHARMACYOFFLINE@YBL-YESB0YBLUPI-500989222413-PAYMENT FOR 154672
  direction: OUTFLOW | amount: 127.5 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=Apollo Pharmacy | counterparty_category=Healthcare & Pharmacy

Example 5 — UPI P2M utility (Jio is a telecom/utility, NOT a subscription):
  desc: UPI-RELIANCE JIO INFOCOM-JIO@CITIBANK-CITI0RTGSMI-501647277334-JIO20BR000BO3CODK1
  direction: OUTFLOW | amount: 349 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=Reliance Jio Infocom | counterparty_category=Utilities & Internet

Example 6 — UPI P2M broadband (JioFiber is a utility):
  desc: UPI-JIOFIBER PREPAID-JIOFIBERPREPAID@PAYTM-YESB0PTMUPI-500611882017-OIDS8000072010@REL
  direction: OUTFLOW | amount: 1178.82 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=JioFiber Prepaid | counterparty_category=Utilities & Internet

Example 7 — UPI P2M OTT subscription (JioCinema IS a subscription):
  desc: UPI-JIOCINEMA-VIACOM18ONLINE@YBL-YESB0YBLUPI-500779917590-SUBSCRIPTION DEBIT
  direction: OUTFLOW | amount: 29 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=JioCinema - Viacom18 | counterparty_category=Mobile, OTT & Subscriptions

Example 8 — BANK credit card bill payment:
  desc: IB BILLPAY DR-HDFC4U-526873XXXXXX1905
  direction: OUTFLOW | amount: 30000 | channel: BANK
  → txn_type=CARD_PAYMENT | counterparty=HDFC Credit Card | counterparty_category=Financial Services, Insurance & Banking

Example 9 — BANK salary (counterparty = the person receiving salary, extracted from narration):
  desc: NEFT CR-RATN0000999-TIDEPLATFO-SASHANK SAI KUPPA-RATNN52025020104799811
  direction: INFLOW | amount: 127557 | channel: BANK
  → txn_type=INCOME_SALARY | counterparty=Sashank Sai Kuppa | counterparty_category=Salary & Income

Example 10 — BANK tax refund:
  desc: NEFT CR-SBIN0000TBU-ITDTAX REFUND 2025-26 IQCPK1665P-SAI SASHANK KUPPA
  direction: INFLOW | amount: 92370 | channel: BANK
  → txn_type=INCOME_OTHER | counterparty=IT Department Tax Refund | counterparty_category=Salary & Income

Example 11 — BANK loan EMI:
  desc: ACH D- IDFC FIRST BANK-1667588167
  direction: OUTFLOW | amount: 9308 | channel: BANK
  → txn_type=LOAN_INSURANCE_PAYMENT | counterparty=IDFC FIRST Bank | counterparty_category=Financial Services, Insurance & Banking

Example 12 — UPI Google Pay mobile recharge ("GPAYRECHARGE" in handle = phone plan recharge, NOT a utility bill):
  desc: UPI-GOOGLE INDIA SERVICE-GPAYRECHARGE@ICICI-ICIC0DC0099-101376305504-UPI
  direction: OUTFLOW | amount: 399 | channel: UPI
  → txn_type=UPI_EXPENSE | upi_type=P2M | counterparty=Google Pay Recharge | counterparty_category=Mobile, OTT & Subscriptions

Example 13 — UPI inflow from an individual (friend sending money back — this is UPI_TRANSFER, NOT INCOME_OTHER):
  desc: UPI-SATYANSH RAI-9151178228@PTYES-SBIN0012980-687912494165-SENT USING
  direction: INFLOW | amount: 500 | channel: UPI
  → txn_type=UPI_TRANSFER | upi_type=P2P | counterparty=Satyansh Rai | counterparty_category=Gifts & Personal Transfers"""


# ── Strategy A: Single-pass (all fields at once) ──────────────────────────

def batch_classify_prompt(
    items: list[dict[str, str]],
) -> tuple[str, str]:
    """Build a single-pass prompt that classifies a batch of transactions.

    Each item has keys: id, txn_date, desc, direction, amount, channel,
    txn_type, upi_type, ref_number, needs.

    Returns (system_message, user_message).
    """

    system = f"""\
You are a financial transaction classifier for Indian bank statements.

For each transaction, fill in ONLY the requested fields from these allowed values:

txn_type (if requested): {_TXN_TYPES}
upi_type (if requested): {_UPI_TYPES}
counterparty: A short, clean, human-readable name (2-4 words max).
counterparty_category (if requested): {_CATEGORIES}

Classification rules:
- UPI_EXPENSE: UPI payment to a merchant/business/service.
- UPI_TRANSFER: UPI payment to an individual person.
- SELF_TRANSFER: Transfer between own accounts or to own UPI Lite wallet.
- BANK_TRANSFER: NEFT/IMPS transfer that is NOT a self-transfer.
- P2M: UPI to a business. P2P: UPI to a person.
- For counterparty, extract the most recognizable consumer-facing brand or person name.

Counterparty naming guidance:
- For salary from TIDEPLATFO or PAYROLL: counterparty = the employee name from the narration.
- For IB BILLPAY DR: counterparty = "HDFC Credit Card" (not "HDFC Bank").
- For STERLING RENT: counterparty = "Sterling Rent".
- For ACH insurance/loan debits: counterparty = the financial institution name.

Category disambiguation:
- "Swiggy" category is ONLY for Swiggy/Instamart transactions.
- "Utilities & Internet": Reliance Jio (mobile recharge), JioFiber, Airtel, gas agencies, electricity. These are utility bills, NOT subscriptions.
- "Mobile, OTT & Subscriptions": Spotify, Netflix, JioCinema, YouTube Premium, app subscriptions. These are entertainment/media subscriptions.
- "Transport & Fuel": Uber, Ola, fuel stations, auto-rickshaws, parking, FASTag.
- "Shopping & E-commerce": Amazon, Flipkart, Myntra, and local retail shops.
- "Entertainment & Events": Cinemas, malls (entry/events), concert venues, amusement parks.
- Do NOT confuse small BharatPe/PayTM QR merchant payments with person-to-person transfers.

IMPORTANT for counterparty_category: First identify the txn_type and counterparty, \
then use their combination to pick the best category. For example, \
"UPI_EXPENSE Spotify" → "Mobile, OTT & Subscriptions", \
"UPI_EXPENSE Reliance Jio Infocom" → "Utilities & Internet".

{_FEW_SHOT}

Respond with ONLY a JSON array. Each element must have "id" and then the requested fields.
No markdown, no explanation, no extra text — just the JSON array."""

    lines = []
    for item in items:
        parts = [f'"id":"{item["id"]}"']
        parts.append(f'"desc":"{item["desc"]}"')
        parts.append(f'"date":"{item.get("txn_date", "")}"')
        parts.append(f'"direction":"{item["direction"]}"')
        parts.append(f'"amount":"{item["amount"]}"')
        if item.get("channel"):
            parts.append(f'"channel":"{item["channel"]}"')
        if item.get("txn_type"):
            parts.append(f'"txn_type_known":"{item["txn_type"]}"')
        if item.get("upi_type"):
            parts.append(f'"upi_type_known":"{item["upi_type"]}"')
        parts.append(f'"need":[{item["needs"]}]')
        lines.append("{" + ", ".join(parts) + "}")

    user = "Classify these transactions:\n\n" + "\n".join(lines)

    return system, user


# ── Strategy B: Two-pass prompts ───────────────────────────────────────────

def two_pass_fields_prompt(
    items: list[dict[str, str]],
) -> tuple[str, str]:
    """Pass 1 of two-pass strategy: get txn_type, upi_type, counterparty.

    Does NOT ask for counterparty_category — that comes in pass 2.
    """

    system = f"""\
You are a financial transaction classifier for Indian bank statements.

For each transaction, determine these fields:

txn_type: {_TXN_TYPES}
upi_type (only for UPI channel): {_UPI_TYPES}
counterparty: A short, clean, human-readable name (2-4 words max).

Classification rules:
- UPI_EXPENSE: UPI payment to a merchant/business/service.
- UPI_TRANSFER: UPI payment to an individual person.
- SELF_TRANSFER: Transfer between own accounts or to own UPI Lite wallet.
- BANK_TRANSFER: NEFT/IMPS transfer that is NOT a self-transfer.
- P2M: UPI to a business. P2P: UPI to a person.
- For counterparty, extract the most recognizable consumer-facing brand or person name.
- Nominal UPI P2P amounts (~₹80-₹1200, not round numbers) to unknown persons are often Uber/Ola rides — classify counterparty as "Uber".

{_FEW_SHOT}

Respond with ONLY a JSON array. Each element must have "id", and whichever of \
"txn_type", "upi_type", "counterparty" are requested.
No markdown, no explanation, no extra text — just the JSON array."""

    lines = []
    for item in items:
        # Figure out which pass-1 fields are still needed
        needed_fields = []
        for f in ("txn_type", "upi_type", "counterparty"):
            if f'"{f}"' in item.get("needs", ""):
                needed_fields.append(f)

        # If rules already filled all three, skip (shouldn't happen often)
        if not needed_fields:
            needed_fields = ["counterparty"]

        parts = [f'"id":"{item["id"]}"']
        parts.append(f'"desc":"{item["desc"]}"')
        parts.append(f'"date":"{item.get("txn_date", "")}"')
        parts.append(f'"direction":"{item["direction"]}"')
        parts.append(f'"amount":"{item["amount"]}"')
        if item.get("channel"):
            parts.append(f'"channel":"{item["channel"]}"')
        if item.get("txn_type"):
            parts.append(f'"txn_type_known":"{item["txn_type"]}"')
        if item.get("upi_type"):
            parts.append(f'"upi_type_known":"{item["upi_type"]}"')
        need_str = ", ".join(f'"{f}"' for f in needed_fields)
        parts.append(f'"need":[{need_str}]')
        lines.append("{" + ", ".join(parts) + "}")

    user = "Classify these transactions (pass 1 — type & counterparty only):\n\n" + "\n".join(lines)

    return system, user


def two_pass_category_prompt(
    items: list[dict[str, str]],
) -> tuple[str, str]:
    """Pass 2 of two-pass strategy: get counterparty_category from txn_type + counterparty.

    Each item should have "id", "txn_type_counterparty" (e.g. "UPI_EXPENSE Spotify"),
    plus full transaction context.
    """

    system = f"""\
You are a financial transaction categoriser for Indian bank statements.

For each transaction, you are given a "txn_type + counterparty" string — use this \
as the primary signal to choose the single best counterparty_category.

Allowed categories: {_CATEGORIES}

Key rules:
- "Swiggy" category is ONLY for Swiggy/Instamart transactions.
- "Transport & Fuel" includes Uber, Ola, fuel stations, auto-rickshaws, cabs.
- "Self Transfer" for transfers between own accounts.
- "Salary & Income" for salary and tax refunds.
- "Friends and Family" for person-to-person transfers with friends/family.
- "Financial Services Insurance & Banking" for loan EMIs, insurance, bank fees.

Examples:
  "UPI_EXPENSE Spotify" → Mobile, OTT & Subscriptions
  "UPI_TRANSFER Nimish Gupta" → Friends and Family
  "UPI_EXPENSE Swiggy" → Swiggy
  "UPI_TRANSFER Uber" → Transport & Fuel
  "INCOME_SALARY Sashank Sai Kuppa" → Salary & Income
  "LOAN_INSURANCE_PAYMENT IDFC FIRST Bank" → Financial Services, Insurance & Banking
  "EXPENSE_OTHER Sterling Rent" → Rent & Housing
  "SELF_TRANSFER VINOD" → Self Transfer
  "UPI_EXPENSE Third Wave Coffee" → Food & Dining
  "INCOME_OTHER IT Department Tax Refund" → Salary & Income

Respond with ONLY a JSON array. Each element: {{"id": "...", "counterparty_category": "..."}}.
No markdown, no explanation, no extra text — just the JSON array."""

    lines = []
    for item in items:
        parts = [f'"id":"{item["id"]}"']
        parts.append(f'"txn_type_counterparty":"{item["txn_type_counterparty"]}"')
        parts.append(f'"direction":"{item["direction"]}"')
        parts.append(f'"amount":"{item["amount"]}"')
        if item.get("channel"):
            parts.append(f'"channel":"{item["channel"]}"')
        lines.append("{" + ", ".join(parts) + "}")

    user = "Categorise these transactions (pass 2 — category only):\n\n" + "\n".join(lines)

    return system, user
