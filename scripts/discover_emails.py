"""
Step 3a: Email Discovery Script

Run this ONCE to:
  1. Trigger the Gmail OAuth flow (opens browser — approve it)
  2. Fetch recent emails from all configured bank senders
  3. Print subject lines so we can see the exact patterns
  4. Save sample HTML bodies as test fixtures for parser development

Usage:
    python3 scripts/discover_emails.py

Output:
    - Console: subject lines grouped by sender
    - Files: tests/fixtures/email_samples/<sender_slug>_<N>.html
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

# Make sure we can import from the repo root regardless of where the script is run from.
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scraper.config import ALL_SENDERS, SCRAPER_LOOKBACK_DAYS
from scraper.gmail_client import GmailClient

# ─── Config ────────────────────────────────────────────────────────────────────

# How far back to look for sample emails.
# 30 days gives us a good variety of transaction types to build parsers against.
DISCOVERY_LOOKBACK_DAYS = 30

# Max samples to save per sender (keeps the fixtures folder tidy).
MAX_SAMPLES_PER_SENDER = 5

# Where to save the HTML fixture files.
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "email_samples"


# ─── Helpers ───────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Turn an email address into a safe filename prefix, e.g. alerts@hdfcbank.net → alerts_hdfcbank_net"""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def print_divider(title: str = "") -> None:
    width = 70
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'─' * pad} {title} {'─' * pad}")
    else:
        print("─" * width)


# ─── Main discovery logic ──────────────────────────────────────────────────────

def main() -> None:
    print_divider("Arth Gmail Discovery — Step 3a")
    print(f"Looking back {DISCOVERY_LOOKBACK_DAYS} days from today.")
    print(f"Fixtures will be saved to: {FIXTURES_DIR}\n")

    # ── Step 1: Authenticate ────────────────────────────────────────────────────
    print("Authenticating with Gmail...")
    print("(If this is your first run, a browser window will open.)\n")

    client = GmailClient()
    client.authenticate()
    print("✓ Authenticated successfully.\n")

    # ── Step 2: Create fixtures directory ──────────────────────────────────────
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    after_date = datetime.date.today() - datetime.timedelta(days=DISCOVERY_LOOKBACK_DAYS)

    # ── Step 3: Fetch and inspect emails from each sender ──────────────────────
    grand_total = 0

    for sender in sorted(ALL_SENDERS):
        print_divider(sender)

        messages = client.fetch_emails(sender=sender, after_date=after_date, max_results=50)

        if not messages:
            print(f"  (no emails found in the last {DISCOVERY_LOOKBACK_DAYS} days)")
            continue

        grand_total += len(messages)
        print(f"  Found {len(messages)} email(s)\n")

        # Print all subject lines so we can see the exact patterns.
        print("  Subject lines:")
        for i, msg in enumerate(messages, start=1):
            date_str = msg.received_at.strftime("%Y-%m-%d")
            print(f"    [{i:>2}] {date_str}  |  {msg.subject}")

        # Save up to MAX_SAMPLES_PER_SENDER HTML bodies as fixtures.
        print(f"\n  Saving up to {MAX_SAMPLES_PER_SENDER} HTML samples...")
        sender_slug = slugify(sender)
        saved = 0

        for i, msg in enumerate(messages[:MAX_SAMPLES_PER_SENDER], start=1):
            try:
                html = client.get_message_body(msg.id)
                if not html.strip():
                    print(f"    [{i}] SKIP (empty body) — {msg.subject}")
                    continue

                filename = FIXTURES_DIR / f"{sender_slug}_{i:02d}.html"
                filename.write_text(html, encoding="utf-8")
                print(f"    [{i}] Saved {filename.name}  ({len(html):,} chars) — {msg.subject}")
                saved += 1

            except Exception as e:
                print(f"    [{i}] ERROR fetching body: {e}")

        print(f"\n  Saved {saved} fixture(s) for {sender}.")

    # ── Step 4: Summary ────────────────────────────────────────────────────────
    print_divider("Summary")
    print(f"Total emails found across all senders: {grand_total}")
    print(f"Fixture files saved to: {FIXTURES_DIR}")
    print("\nNext step:")
    print("  Review the subject lines above and the saved HTML files.")
    print("  For pinned parser fixtures (test filenames), use:")
    print("    python3 scripts/sync_email_parser_fixtures.py --dry-run")
    print_divider()


if __name__ == "__main__":
    main()
