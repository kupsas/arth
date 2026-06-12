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
import os

# ─── Repo root (two levels up from this file: scraper/config.py → repo root) ──
REPO_ROOT = Path(__file__).parent.parent

# ─── OAuth credential file paths ───────────────────────────────────────────────
# gmail_credentials.json — OAuth desktop client (shipped with the repo for convenience).
# gmail_token.json — created on first Google sign-in per machine (gitignored).
GMAIL_CREDENTIALS_PATH = REPO_ROOT / "data" / "gmail_credentials.json"
GMAIL_TOKEN_PATH       = REPO_ROOT / "data" / "gmail_token.json"

# Desktop OAuth callback — fixed port so Docker can publish it and the dashboard can open Google in-browser.
GMAIL_OAUTH_CALLBACK_PORT = int(os.getenv("ARTH_GMAIL_OAUTH_CALLBACK_PORT", "8090"))
GMAIL_OAUTH_BIND_HOST = os.getenv("ARTH_GMAIL_OAUTH_BIND_HOST", "0.0.0.0").strip()
GMAIL_OAUTH_REDIRECT_HOST = os.getenv("ARTH_GMAIL_OAUTH_REDIRECT_HOST", "127.0.0.1").strip()

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
#       "instrument_type": str,     # savings | credit_card | broker (coarse bucket for wizard)
#       "discovery_subject_patterns": list[str],  # regexes matched against Subject during Gmail discovery
#       "expected_cadence": str,   # annual | yearly | quarterly | monthly | per_transaction
#       "gmail_subject_filter_keywords": list[str],  # optional — onboarding Gmail queries use
#                                   # one ``subject:"keyword"`` search per entry (noisy senders)
#   }
#
# The "last_4_digits" key is what appears in the email body (card/account number).
# Parsers use this lookup to stamp the correct account_id on each ParsedTransaction.

# Per-user last-4 → account_id / source_key lives in SQLite
# (``scraper_account_mappings``), not in this file.  Empty ``accounts`` here means
# ``get_bank_senders_config`` falls back to this template only when the DB has no
# rows — run ``python scripts/migrate_sashank_config_to_db.py`` once for your user,
# or complete onboarding ``POST /api/onboarding/persist-sources``.

# ICICI savings — transaction alerts + statement PDFs share the same shape; mappings from DB.
_ICICI_STATEMENT_ACCOUNTS: dict[str, dict] = {}

# HDFC transaction alerts (.net / .bank.in) — mappings from DB.
_HDFC_BANK_ACCOUNTS: dict[str, dict] = {}

# HDFC Card Statement PDF — mappings from DB.
_HDFC_CC_STATEMENT_ACCOUNTS: dict[str, dict] = {}

# ICICI Securities statement emails (equity + MF PDFs; ``parser_key`` for onboarding DB).
_ICICI_DIRECT_BROKER_ACCOUNTS: dict[str, dict] = {
    "0000": {
        "account_id": "ICICI_DIRECT",
        "source_key": "icici_direct_statement",
    },
}

_ZERODHA_BROKER_ACCOUNTS: dict[str, dict] = {
    "0000": {
        "account_id": "ZERODHA",
        "source_key": "zerodha_demat_statement",
    },
}

# Shared discovery regex snippets (Subject line hints; case-insensitive).
_PAT_HDFC_ALERT = [r"(?i)Insta\s*Alert", r"(?i)HDFC"]
_PAT_ICICI_NOTIF = [r"(?i)ICICI", r"(?i)Transaction"]
_PAT_ICICI_STMT = [r"(?i)e-?\s*Statement", r"(?i)ICICI", r"(?i)Account"]
_PAT_HDFC_CC_STMT = [r"(?i)Credit\s*Card", r"(?i)Statement", r"(?i)HDFC"]
_PAT_HDFC_COMBINED = [r"(?i)Smart\s*Statement", r"(?i)Combined", r"(?i)HDFC"]
_PAT_ICICI_DIRECT_STMT = [
    r"(?i)Equity\s+Transaction\s+Statement",
    r"(?i)Mutual\s+Fund\s+Account\s+Statement",
]
_PAT_ZERODHA_DEMAT = [
    r"(?i)Zerodha",
    r"(?i)Monthly\s+Demat\s+Transaction",
]
_PAT_SBI_STMT = [
    r"(?i)E-account\s+statement",
    r"(?i)SBI",
    r"(?i)account",
]

# SBI e-account (CAS) monthly statement — per-user last-4 mappings from DB.
_SBI_STATEMENT_ACCOUNTS: dict[str, dict] = {}

BANK_SENDERS: dict[str, dict] = {
    "alerts@hdfcbank.net": {
        "parser_key": "hdfc_bank",
        "accounts": _HDFC_BANK_ACCOUNTS,
        "display_name": "HDFC Bank transaction alerts",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_ALERT,
        "expected_cadence": "per_transaction",
        # Without this, onboarding runs ``from:alerts@…`` alone — tens of thousands of
        # marketing / OTP / non-transaction mails match, then one metadata GET per row.
        # Keywords align with :mod:`parsers.alerts.hdfc` ``can_parse`` subject gates.
        "gmail_subject_filter_keywords": [
            "UPI txn",
            "Account update for your HDFC Bank A/c",
            "debited via Credit Card",
            "A payment was made using your Credit Card",
        ],
    },
    "alerts@hdfcbank.bank.in": {
        "parser_key": "hdfc_bank",
        "accounts": _HDFC_BANK_ACCOUNTS,
        "display_name": "HDFC Bank transaction alerts (.bank.in)",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_ALERT,
        "expected_cadence": "per_transaction",
        "gmail_subject_filter_keywords": [
            "UPI txn",
            "Account update for your HDFC Bank A/c",
            "debited via Credit Card",
            "A payment was made using your Credit Card",
        ],
    },
    # Note: ICICI transaction alerts come from the .bank.in domain (NOT .com).
    # customercare@icicibank.com sends MAB reminders and marketing — not transaction alerts.
    "customernotification@icici.bank.in": {
        "parser_key": "icici_bank",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "display_name": "ICICI Bank transaction alerts",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_NOTIF,
        "expected_cadence": "per_transaction",
    },
    # ICICI savings statement PDFs (password-protected attachment — not transaction alerts).
    # Monthly (current + legacy): estatement may use .com or .bank.in; annual FY: .com below.
    "estatement@icicibank.com": {
        "parser_key": "icici_statement",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "ICICI e-Statement (.com)",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_STMT,
        "expected_cadence": "monthly",
    },
    "estatement@icici.bank.in": {
        "parser_key": "icici_statement",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "ICICI e-Statement (.bank.in)",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_STMT,
        "expected_cadence": "monthly",
    },
    "customernotification@icicibank.com": {
        "parser_key": "icici_statement",
        "accounts": _ICICI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "ICICI statement notifications (.com)",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_ICICI_STMT,
        "expected_cadence": "monthly",
    },
    # Credit card monthly PDF — From varies (.net vs .bank.in); see email-statement plan.
    "emailstatements.cards@hdfcbank.net": {
        "parser_key": "hdfc_cc_statement",
        "accounts": _HDFC_CC_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "HDFC Credit Card e-statements",
        "instrument_type": "credit_card",
        "discovery_subject_patterns": _PAT_HDFC_CC_STMT,
        "expected_cadence": "monthly",
    },
    "emailstatements.cards@hdfcbank.bank.in": {
        "parser_key": "hdfc_cc_statement",
        "accounts": _HDFC_CC_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "HDFC Credit Card e-statements (.bank.in)",
        "instrument_type": "credit_card",
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
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_COMBINED,
        "expected_cadence": "monthly",
    },
    "hdfcbanksmartstatement@hdfcbank.bank.in": {
        "parser_key": "hdfc_combined_statement",
        "accounts": dict(_HDFC_BANK_ACCOUNTS),
        "first_run_lookback_days": 45,
        "display_name": "HDFC Smart / combined statement (.bank.in)",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_HDFC_COMBINED,
        "expected_cadence": "monthly",
    },
    # ICICI Securities — equity / MF **statement** PDFs (password: ICICI_DIRECT_STATEMENT_PASSWORD_KEYS).
    # Email parsers + registry: WS1 Phase 2. Listed here for Gmail discovery / onboarding.
    "service@icicisecurities.com": {
        "parser_key": "icici_direct_statement",
        "accounts": _ICICI_DIRECT_BROKER_ACCOUNTS,
        "first_run_lookback_days": 120,
        "display_name": "ICICI Direct broker statements (equity + MF)",
        "instrument_type": "broker",
        "discovery_subject_patterns": _PAT_ICICI_DIRECT_STMT,
        "expected_cadence": "quarterly",
        # Narrow Gmail searches — this sender also pushes portfolio/KYC/scheme noise.
        # See onboarding ``_collect_pending_queue`` / ``gmail_subject_filter_keywords``.
        "gmail_subject_filter_keywords": [
            "Equity Transaction Statement",
            "Mutual Fund Account Statement",
        ],
    },
    "no-reply-transaction-with-holding-statement@reportsmailer.zerodha.net": {
        "parser_key": "zerodha_demat_statement",
        "accounts": _ZERODHA_BROKER_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "Zerodha monthly demat transaction statement",
        "instrument_type": "broker",
        "discovery_subject_patterns": _PAT_ZERODHA_DEMAT,
        "expected_cadence": "monthly",
        "gmail_subject_filter_keywords": [
            "Monthly Demat Transaction",
        ],
    },
    "cbssbi.cas@alerts.sbi.bank.in": {
        "parser_key": "sbi_statement",
        "accounts": _SBI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "SBI e-account statement",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_SBI_STMT,
        "expected_cadence": "monthly",
        "gmail_subject_filter_keywords": [
            "E-account statement",
        ],
    },
    "cbssbi.cas@alerts.sbi.co.in": {
        "parser_key": "sbi_statement",
        "accounts": _SBI_STATEMENT_ACCOUNTS,
        "first_run_lookback_days": 45,
        "display_name": "SBI e-account statement (.co.in)",
        "instrument_type": "savings",
        "discovery_subject_patterns": _PAT_SBI_STMT,
        "expected_cadence": "monthly",
        "gmail_subject_filter_keywords": [
            "E-account statement",
        ],
    },
}

# Gmail From addresses that carry ICICI **savings** statement PDFs (monthly or annual FY).
# Used by :mod:`parsers.statements.icici` to recognise FY subjects regardless
# of whether ICICI used ``estatement@…`` or ``customernotification@icicibank.com``.
# **Exclude** ``customernotification@icici.bank.in`` — that domain is transaction alerts only today.
# When ICICI routes FY PDFs from a new ``*@icici.bank.in`` mailbox, add it here and in
# ``BANK_SENDERS`` with ``parser_key="icici_statement"``.
ICICI_SAVINGS_STATEMENT_SENDERS: frozenset[str] = frozenset(
    {
        "estatement@icicibank.com",
        "estatement@icici.bank.in",
        "customernotification@icicibank.com",
    }
)

# Convenience set — all sender addresses we care about (used for Gmail queries).
ALL_SENDERS: set[str] = set(BANK_SENDERS.keys())
