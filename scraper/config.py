"""
Scraper configuration — bank sender addresses, account mappings, file paths.

Per-user last-4 → account_id / source_key is stored in SQLite
(``scraper_account_mappings``).  This module keeps **generic** Gmail sender metadata
(parser_key, display_name, discovery regexes, expected_cadence) so discovery and
code defaults stay in sync.  For a populated desktop DB, run once::

    python scripts/migrate_sashank_config_to_db.py

or use onboarding ``POST /api/onboarding/persist-sources`` after Gmail discovery.
"""

from pathlib import Path

# ─── Repo root (two levels up from this file: scraper/config.py → repo root) ──
REPO_ROOT = Path(__file__).parent.parent

# ─── OAuth credential file paths ───────────────────────────────────────────────
# credentials.json is downloaded once from GCP console (never committed to git).
# token.json is created automatically on first run after OAuth consent.
GMAIL_CREDENTIALS_PATH = REPO_ROOT / "data" / "gmail_credentials.json"
GMAIL_TOKEN_PATH       = REPO_ROOT / "data" / "gmail_token.json"

# ─── Scraper behaviour ─────────────────────────────────────────────────────────
# On the very first run (no processed_emails rows yet), how far back should we
# look for emails?  7 days is conservative and avoids flooding the DB.
SCRAPER_LOOKBACK_DAYS = 7

# How often the APScheduler polls Gmail (used in scheduler.py).
POLL_INTERVAL_MINUTES = 15

# ─── Gmail scopes ──────────────────────────────────────────────────────────────
# readonly is enough — we never send, delete, or modify emails.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# ─── Bank sender → parser + account mapping ────────────────────────────────────
# Structure:
#   sender_email: {
#       "parser_key": str,          # matches a key in EMAIL_PARSER_REGISTRY (email_parsers/__init__.py)
#       "accounts": { ... },       # as below
#       "display_name": str,       # human label for onboarding / UI
#       "source_type": str,        # savings | credit_card | broker (coarse bucket for wizard)
#       "discovery_subject_patterns": list[str],  # regexes matched against Subject during Gmail discovery
#       "expected_cadence": str,   # annual | yearly | quarterly | monthly | per_transaction
#   }
#
# The "last_4_digits" key is what appears in the email body (card/account number).
# Parsers use this lookup to stamp the correct account_id on each ParsedTransaction.

# Per-user last-4 → account_id / source_key lives in SQLite
# (``scraper_account_mappings``), not in this file.  Empty ``accounts`` here means
# ``get_bank_senders_config`` falls back to this template only when the DB has no
# rows — run ``python scripts/migrate_sashank_config_to_db.py`` once for your user,
# or complete onboarding ``POST /api/onboarding/persist-sources``.

# ICICI savings — InstaAlerts + statement PDFs share the same shape; mappings from DB.
_ICICI_STATEMENT_ACCOUNTS: dict[str, dict] = {}

# HDFC InstaAlerts (.net / .bank.in) — mappings from DB.
_HDFC_BANK_ACCOUNTS: dict[str, dict] = {}

# HDFC Card Statement PDF — mappings from DB.
_HDFC_CC_STATEMENT_ACCOUNTS: dict[str, dict] = {}

# ICICI Direct / NSE trade PDFs — ``last_4`` is a structural placeholder (not a card).
_ICICI_DIRECT_TRADE_ACCOUNTS: dict[str, dict] = {
    "0000": {
        "account_id": "ICICI_DIRECT",
        "source_key": "icici_direct_equity",
    },
}

# ICICI Securities statement emails (equity + MF PDFs; ``parser_key`` for onboarding DB).
# Router entries are added when email parsers land (WS1 Phase 2).
_ICICI_DIRECT_BROKER_ACCOUNTS: dict[str, dict] = {
    "0000": {
        "account_id": "ICICI_DIRECT",
        "source_key": "icici_direct_equity",
    },
}

# Shared discovery regex snippets (Subject line hints; case-insensitive).
_PAT_HDFC_INSTA = [r"(?i)Insta\s*Alert", r"(?i)HDFC"]
_PAT_ICICI_NOTIF = [r"(?i)ICICI", r"(?i)Transaction"]
_PAT_ICICI_STMT = [r"(?i)e-?\s*Statement", r"(?i)ICICI", r"(?i)Account"]
_PAT_HDFC_CC_STMT = [r"(?i)Credit\s*Card", r"(?i)Statement", r"(?i)HDFC"]
_PAT_HDFC_COMBINED = [r"(?i)Smart\s*Statement", r"(?i)Combined", r"(?i)HDFC"]
_PAT_NSE_TRADE = [r"(?i)Trades?\s+executed", r"(?i)NSE"]
_PAT_ICICI_DIRECT_STMT = [
    r"(?i)Equity\s+Transaction\s+Statement",
    r"(?i)Mutual\s+Fund\s+Account\s+Statement",
]

BANK_SENDERS: dict[str, dict] = {
    "alerts@hdfcbank.net": {
        "parser_key": "hdfc_bank",
        "accounts": _HDFC_BANK_ACCOUNTS,
        "display_name": "HDFC Bank InstaAlerts",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_INSTA,
        "expected_cadence": "per_transaction",
    },
    "alerts@hdfcbank.bank.in": {
        "parser_key": "hdfc_bank",
        "accounts": _HDFC_BANK_ACCOUNTS,
        "display_name": "HDFC Bank InstaAlerts (.bank.in)",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_INSTA,
        "expected_cadence": "per_transaction",
    },
    # Note: ICICI transaction alerts come from the .bank.in domain (NOT .com).
    # customercare@icicibank.com sends MAB reminders and marketing — not transaction alerts.
    "customernotification@icici.bank.in": {
        "parser_key": "icici_bank",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "display_name": "ICICI Bank InstaAlerts",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_NOTIF,
        "expected_cadence": "per_transaction",
    },
    # ICICI savings statement PDFs (password-protected attachment — not InstaAlerts).
    # Monthly (current + legacy): estatement may use .com or .bank.in; annual FY: .com below.
    "estatement@icicibank.com": {
        "parser_key": "icici_statement",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "ICICI e-Statement (.com)",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_STMT,
        "expected_cadence": "monthly",
    },
    "estatement@icici.bank.in": {
        "parser_key": "icici_statement",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "ICICI e-Statement (.bank.in)",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_STMT,
        "expected_cadence": "monthly",
    },
    "customernotification@icicibank.com": {
        "parser_key": "icici_statement",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "ICICI statement notifications (.com)",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_STMT,
        "expected_cadence": "monthly",
    },
    # Credit card monthly PDF — From varies (.net vs .bank.in); see email-statement plan.
    "emailstatements.cards@hdfcbank.net": {
        "parser_key": "hdfc_cc_statement",
        "accounts": _HDFC_CC_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "HDFC Credit Card e-statements",
        "source_type": "credit_card",
        "discovery_subject_patterns": _PAT_HDFC_CC_STMT,
        "expected_cadence": "monthly",
    },
    "emailstatements.cards@hdfcbank.bank.in": {
        "parser_key": "hdfc_cc_statement",
        "accounts": _HDFC_CC_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "HDFC Credit Card e-statements (.bank.in)",
        "source_type": "credit_card",
        "discovery_subject_patterns": _PAT_HDFC_CC_STMT,
        "expected_cadence": "monthly",
    },
    # HDFC combined monthly statement PDF (savings 3703) — same "Smart Statement" sender
    # as pre-2024 "Email Account Statement"; we only parse **combined** subjects (see parser).
    "hdfcbanksmartstatement@hdfcbank.net": {
        "parser_key": "hdfc_combined_statement",
        "accounts": dict(_HDFC_BANK_ACCOUNTS),
        "first_run_lookback_days": 45,
        "display_name": "HDFC Smart / combined statement",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_COMBINED,
        "expected_cadence": "monthly",
    },
    "hdfcbanksmartstatement@hdfcbank.bank.in": {
        "parser_key": "hdfc_combined_statement",
        "accounts": dict(_HDFC_BANK_ACCOUNTS),
        "first_run_lookback_days": 45,
        "display_name": "HDFC Smart / combined statement (.bank.in)",
        "source_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_COMBINED,
        "expected_cadence": "monthly",
    },
    # NSE — *Trades executed at NSE* PDF only (``NSE_TRADES_EXECUTED_PASSWORD``). Add your
    # mailbox's From: here if it differs (router still requires that subject line).
    "ebix@nse.co.in": {
        "parser_key": "icici_direct_trade",
        "accounts": _ICICI_DIRECT_TRADE_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "NSE trade confirmations (ebix)",
        "source_type": "broker",
        "discovery_subject_patterns": _PAT_NSE_TRADE,
        "expected_cadence": "per_transaction",
    },
    "nseinvest@nse.co.in": {
        "parser_key": "icici_direct_trade",
        "accounts": _ICICI_DIRECT_TRADE_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "NSE trade confirmations (nseinvest)",
        "source_type": "broker",
        "discovery_subject_patterns": _PAT_NSE_TRADE,
        "expected_cadence": "per_transaction",
    },
    "nse-direct@nse.co.in": {
        "parser_key": "icici_direct_trade",
        "accounts": _ICICI_DIRECT_TRADE_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "NSE trade confirmations (nse-direct)",
        "source_type": "broker",
        "discovery_subject_patterns": _PAT_NSE_TRADE,
        "expected_cadence": "per_transaction",
    },
    # ICICI Securities — equity / MF **statement** PDFs (password: ICICI_DIRECT_STATEMENT_PASSWORD_KEYS).
    # Email parsers + registry: WS1 Phase 2. Listed here for Gmail discovery / onboarding.
    "service@icicisecurities.com": {
        "parser_key": "icici_direct_statement",
        "accounts": _ICICI_DIRECT_BROKER_ACCOUNTS,
        "first_run_lookback_days": 120,
        "display_name": "ICICI Direct broker statements (equity + MF)",
        "source_type": "broker",
        "discovery_subject_patterns": _PAT_ICICI_DIRECT_STMT,
        "expected_cadence": "quarterly",
    },
}

# Convenience set — all sender addresses we care about (used for Gmail queries).
ALL_SENDERS: set[str] = set(BANK_SENDERS.keys())
