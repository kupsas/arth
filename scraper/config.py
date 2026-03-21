"""
Scraper configuration — bank sender addresses, account mappings, file paths.

This is the single place to change if your bank email address changes, you add
a new account, or you want to tweak the polling interval.

BANK_SENDERS maps each sender email address to:
  - parser_key: which parser module handles emails from this sender
  - accounts: a dict of last-4-digits → account_id + source_key
              (used by parsers to figure out WHICH account triggered the alert)
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
#       "accounts": {
#           "last_4_digits": {
#               "account_id":   str,   # the account_id stored in the DB (matches pipeline config)
#               "source_key":   str,   # the source_key used by the pipeline (for PipelineRun records)
#           }
#       }
#   }
#
# The "last_4_digits" key is what appears in the email body (card/account number).
# Parsers use this lookup to stamp the correct account_id on each ParsedTransaction.

# HDFC InstaAlerts historically used @hdfcbank.net; many alerts now come from
# @hdfcbank.bank.in ("HDFC Bank InstaAlerts <...>"). Same parsers/accounts.
_HDFC_BANK_ACCOUNTS: dict[str, dict] = {
    "3703": {
        "account_id": "HDFC_SAL_3703",
        "source_key": "hdfc_savings",
    },
    "1905": {
        "account_id": "HDFC_CC_1905",
        "source_key": "hdfc_cc_1905",
    },
    "5778": {
        "account_id": "HDFC_CC_5778",
        "source_key": "hdfc_cc_5778",
    },
}

BANK_SENDERS: dict[str, dict] = {
    "alerts@hdfcbank.net": {
        "parser_key": "hdfc_bank",
        "accounts": _HDFC_BANK_ACCOUNTS,
    },
    "alerts@hdfcbank.bank.in": {
        "parser_key": "hdfc_bank",
        "accounts": _HDFC_BANK_ACCOUNTS,
    },
    # Note: ICICI transaction alerts come from the .bank.in domain (NOT .com).
    # customercare@icicibank.com sends MAB reminders and marketing — not transaction alerts.
    "customernotification@icici.bank.in": {
        "parser_key": "icici_bank",
        "accounts": {
            "6118": {
                "account_id": "ICICI_SAV_6118",
                "source_key": "icici_savings",
            },
        },
    },
}

# Convenience set — all sender addresses we care about (used for Gmail queries).
ALL_SENDERS: set[str] = set(BANK_SENDERS.keys())
