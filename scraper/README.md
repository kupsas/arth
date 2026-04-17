# Gmail Email Scraper

Real-time transaction ingestion via Gmail API. Polls bank alert emails every 15 minutes, parses them into transactions, and automatically reconciles them when monthly statements arrive — no duplicates, no lost review work.

## Ingestion hierarchy (what is “primary”)

**Email scraping is the default path** for ongoing data: alerts, statement PDFs, and broker mail routed in `scraper/email_parsers/`. **File-based ingestion** (`python -m pipeline.run`, API upload, `holding_pipeline` CLI) is the **explicit fallback** for gaps email cannot cover, one-off bank exports, or migrating historical files off disk.

See [`docs/system-design/INGESTION_PATHS.md`](../docs/system-design/INGESTION_PATHS.md) for the full source × path matrix. Reconciliation rules live in [`pipeline/db_writer.py`](../pipeline/db_writer.py) and are summarized below.

**Historical Gmail import:** use `run_historical_backfill` (API: `POST /api/scraper/backfill`), or the CLI `scripts/scrape_historical.py` with `--preset` / `--query` — same pipeline as live scraping; `processed_emails` dedupes by message id.

**Parser test fixtures:** to repopulate `tests/fixtures/email_samples/` from Gmail with pinned queries, use `scripts/sync_email_parser_fixtures.py` (documented in `tests/README.md`).

## Setup

### Step 1 — Google Cloud Project (one-time)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project (or reuse one)
2. Enable the **Gmail API**: APIs & Services → Library → search "Gmail API" → Enable
3. Create OAuth credentials: APIs & Services → Credentials → Create Credentials → OAuth client ID → **Desktop app**
4. Download the JSON → save it as `data/gmail_credentials.json`

Add Gmail vars to your `.env` (see `.env.example` for all options):

```bash
GMAIL_CREDENTIALS_PATH=data/gmail_credentials.json
GMAIL_TOKEN_PATH=data/gmail_token.json
SCRAPER_POLL_INTERVAL_MINUTES=15
SCRAPER_LOOKBACK_DAYS=7
```

### Step 2 — First-run OAuth consent (one-time)

```bash
# Option A: via the API (server must be running)
curl -X GET http://localhost:8000/api/scraper/oauth/init
# Returns a URL → open it in your browser → click Allow

# Option B: via the discovery script (before starting the server)
python3 scripts/discover_emails.py
```

### Step 3 — Verify

```bash
curl http://localhost:8000/api/scraper/oauth/status
# { "is_authenticated": true, ... }
```

The scheduler starts automatically on every server boot. If `gmail_token.json` doesn't exist yet, it starts paused and activates once OAuth completes.

### Manual trigger

```bash
curl -X POST http://localhost:8000/api/scraper/trigger
```

---

## Email Alert Coverage

| Bank / Account      | What email captures                       | What needs a statement            |
| ------------------- | ----------------------------------------- | --------------------------------- |
| HDFC CC (1905/5778) | All CC swipes (real-time)                 | Refunds, cashback, auto-pay       |
| HDFC Savings 3703   | UPI outbound + inbound                    | Net banking transfers, salary     |
| ICICI Savings 6118  | IMPS + NEFT via iMobile (manual triggers) | All inbound, ICICI Direct trades  |

Email scraping covers ~70-80% of day-to-day spending. Monthly statement uploads fill the remaining gaps.

### Statement PDFs and broker emails (Phase 0+)

Beyond **alert** emails, the scraper can process **attached PDFs** and structured broker mail (HDFC combined statements, HDFC CC statement PDFs, ICICI statement PDFs, ICICI Direct trade notifications). These flow through dedicated parsers in `scraper/email_parsers/` (`hdfc_statement.py`, `hdfc_cc_statement.py`, `icici_statement.py`, `icici_direct_trade.py`, etc.) and may enqueue rows for the **review queue** or investment pipeline depending on content. For **large one-off archives** already on disk, the API/dashboard upload path can be easier — email remains the primary path for ongoing months.

**What email does NOT capture (by design):**
- HDFC net banking outbound — HDFC intentionally sends no alert; you initiated it from their platform
- HDFC CC auto-pay / E-mandate — email exists but contains no amount or date, cannot be parsed
- ICICI inbound flows — no alert emails sent
- ICICI Direct / broker trades — no transactional email at all

---

## How Reconciliation Works

The same real-world transaction can arrive via two paths: email alert first, then statement narration later. The statement narration is richer (full UPI ref, closing balance) but has a completely different description string, so a naive hash dedup won't catch the duplicate.

**The solution:** when a statement row arrives, `db_writer.py` fuzzy-matches it against existing email-sourced rows before inserting:

```
For each statement transaction:
  1. Look for an email row where:
       account_id matches exactly
       amount matches exactly
       txn_date is within ±1 day
       source_type = 'email' (not already reconciled)
  2. If match found:
       Upgrade the email row with statement data (description, ref_number, closing_balance)
       Preserve any manual edits (counterparty, category) the user may have made
       Set source_type = 'reconciled', is_reviewed = True
       Do NOT insert a new row
  3. If no match:
       Insert as a new row (source_type = 'statement')
       This is a gap transaction the email never captured
```

**Result:** one row per real-world transaction, always. Manual review work is never lost.

### source_type values on the Transaction table

| Value          | Meaning                                                          |
| -------------- | ---------------------------------------------------------------- |
| `"email"`      | Came from the Gmail scraper; unreviewed until statement arrives  |
| `"statement"`  | Came from a file upload; no matching email row existed           |
| `"reconciled"` | Started as `email`, upgraded when the matching statement arrived |

---

## API Routes

All endpoints are under `/api/scraper`:

| Method  | Path            | Description                                                     |
| ------- | --------------- | --------------------------------------------------------------- |
| `GET`   | `/status`       | Scheduler state: running/paused, last run, next run, counts     |
| `POST`  | `/trigger`      | Run one scrape cycle immediately (async, returns result)        |
| `POST`  | `/start`        | Resume the scheduler                                            |
| `POST`  | `/stop`         | Pause the scheduler                                             |
| `PATCH` | `/config`       | Update poll interval (minutes)                                  |
| `GET`   | `/emails`       | List processed emails — paginated, filterable by status/sender  |
| `GET`   | `/oauth/init`   | Start OAuth2 flow — returns auth URL to open in browser         |
| `GET`   | `/oauth/status` | Check if Gmail token exists and is valid                        |
| `GET`   | `/coverage`     | Coverage map: which accounts have email alerts, which don't     |

---

## Module Structure

```
scraper/
  config.py          Sender addresses, account → account_id mapping, OAuth paths, poll interval
  gmail_client.py    OAuth2 auth, token management, email fetching, HTML body extraction
  email_router.py    find_parser(): routes a message to the correct parser by sender + subject
  orchestrator.py    scrape_new_emails(): the main cycle (fetch → dedup → parse → classify → write)
  scheduler.py       APScheduler wrapper: Gmail poll, daily price job, weekly inflation + weekly market cache job
  email_parsers/
    base.py          BaseEmailParser ABC + _lookup_account() helper
    base_statement.py Shared helpers for statement PDF pipelines
    hdfc_bank.py     HDFC alert parsers (CC swipe, UPI, account update)
    hdfc_statement.py / hdfc_cc_statement.py  HDFC PDF statements
    icici_bank.py    ICICI IMPS/NEFT alert parser
    icici_statement.py  ICICI PDF statements
    icici_direct_trade.py  ICICI Direct trade / contract emails
```

**Adding a new bank parser:**
1. Create `scraper/email_parsers/newbank.py` with a class that extends `BaseEmailParser`
2. Implement `can_parse(sender, subject) -> bool` and `parse(html_body, received_date) -> list[ParsedTransaction]`
3. Register it in `scraper/email_parsers/__init__.py` and add the sender to `scraper/config.py`

---

## Architecture Notes

- **Subject filter before body download:** `find_parser()` checks the subject line first. Non-transaction emails (MAB reminders, marketing, OTT confirmations) are skipped without paying the API cost of a body download.
- **ProcessedEmail table:** Every Gmail message ID that the scraper touches gets recorded in `processed_emails`. This is the dedup layer for the scraper itself — the same email is never processed twice, even across server restarts.
- **Concurrency guard:** A `threading.Lock` prevents two concurrent scrape cycles (e.g. a scheduled poll firing while a manual trigger is in progress). The second call returns the current result immediately.
- **Single GmailClient instance:** One authenticated client is shared across all polling cycles so token refresh is seamless.
- **LLM_MODEL patching in tests:** Tests patch `pipeline.config.LLM_MODEL = "none"` to skip LLM calls. Patch the attribute on the module, not the import.
