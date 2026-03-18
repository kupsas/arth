# Arth

Personal finance transaction pipeline with a SQLite database and FastAPI backend. Reads raw Indian bank statements, classifies transactions using deterministic rules + LLM, stores results in SQLite, and exposes them via a REST API.

## Quick Start

```bash
# 1. Install dependencies
python3 -m pip install -r requirements.txt

# 2. Set up API keys
cp .env.example .env
# Edit .env with your OpenAI / Anthropic / Google API keys

# 3. Populate the database (all 4 sources)
python3 -m pipeline.run --all-sources          # full pipeline with LLM
python3 -m pipeline.run --all-sources --llm none  # rules-only, no LLM

# 4. Run the API server
uvicorn api.main:app --reload --port 8000
# Swagger UI at http://localhost:8000/docs
```

## How It Works

```
Raw Statement (.txt / .csv / .pdf)
        |
   [1] Parse          -- source-specific parser (HDFC savings, ICICI, CC)
        |
   [2] Transform      -- bank-agnostic: assign IDs, ISO dates, direction, amounts
        |
   [3] Rules Classify -- deterministic: channel, txn_type, upi_type where possible
        |
   [4] LLM Classify   -- fills counterparty, category, remaining types
        |
   [5] Write SQLite   -- dedup by content_hash; backfill NULLs on re-runs
```

Adding a new bank = write one parser file. Everything else is bank-agnostic.

## API Endpoints

**Transactions** (`/api/transactions`)
- `GET /` — List with filters: `date_from`, `date_to`, `account_id`, `direction`, `category`, `search`, `page`, `page_size`, `sort_by`
- `GET /{id}` — Single transaction
- `PATCH /{id}` — Update mutable fields (counterparty, category, txn_type, notes, is_reviewed)
- `PATCH /bulk` — Bulk update (e.g. mark multiple as reviewed)

**Pipeline** (`/api/pipeline`)
- `POST /run` — Trigger a pipeline run. Body: `{ "source_key": "hdfc_savings" | "all", "llm_model": "auto" | "none" }`
- `GET /runs` — List past pipeline runs
- `GET /runs/{id}` — Single run status (for polling)

**Scraper** (`/api/scraper`) — see [`scraper/README.md`](scraper/README.md) for full reference

**Health:** `GET /health`

## Environments

| Environment | DB file             | How to use                            |
| ----------- | ------------------- | ------------------------------------- |
| prod        | `data/arth.db`      | `uvicorn api.main:app --port 8000`    |
| test        | `data/arth_test.db` | `APP_ENV=test uvicorn api.main:app --port 8001` |
| pytest      | in-memory SQLite    | `pytest tests/` (no env var needed)   |

## LLM Model Strategy

The pipeline uses a **multi-model fallback chain** so rate limits or outages on any one provider don't block classification.

**Fallback order:**
1. `gemini-3.1-flash-lite` (primary — best quality-to-cost ratio)
2. `gemini-2.5-flash` (same provider, slightly costlier)
3. `claude-haiku-4-5` (Anthropic — different provider)
4. `gpt-5-mini` (OpenAI — different provider)

Full benchmark results and methodology in `docs/evaluations/llm-benchmark-2026-03/`.

## Current Accuracy (March 2026, HDFC savings dataset)

On the full HDFC savings dataset (~647 matched rows) with the latest rules + prompt tuning:

- direction, amount, channel: **100%**
- txn_type: **98.7%**
- upi_type: **98.1%**
- counterparty: **94.9%**
- counterparty_category: **93.7%**

## Data in the Database

| Source        | Transactions | Account ID       |
| ------------- | ------------ | ---------------- |
| HDFC Savings  | 1,699        | HDFC_SAL_3703    |
| HDFC CC 1905  | 952          | HDFC_CC_1905     |
| HDFC CC 5778  | 134          | HDFC_CC_5778     |
| ICICI Savings | 451          | ICICI_SAV_6118   |
| **Total**     | **3,236**    |                  |

## Key Design Notes

- **Rules first, LLM second:** Moving classification into deterministic rules (UPI handle analysis, P2P vs P2M detection, self-transfer indicators, merchant heuristics) dramatically reduced LLM variance and cost. LLM is only called for genuinely ambiguous counterparty names.
- **Dedup by content_hash:** SHA-256 of `(txn_date, raw_description, amount, account_id)`. Re-running the pipeline on the same statement is fully idempotent. Backfill logic fills NULLs without overwriting existing values, preserving manual corrections.
- **Double-counting awareness:** `CARD_EXPENSE` (individual CC swipe) and `CARD_PAYMENT` (paying the CC bill) both exist in the DB. Naively summing all OUTFLOWs double-counts spending. Phase 3 metrics endpoints will filter correctly by `txn_type`.
- **LLM caching:** All LLM responses are cached keyed by batch content hash. Re-running the pipeline after adding new statements only calls the LLM for genuinely new transactions.

## Repository Structure

```
Arth-email-scraper/
  api/                       # FastAPI backend
    main.py                    App entry point, CORS, lifespan, scheduler start/stop
    database.py                Engine, session factory, init_db()
    models.py                  SQLModel table definitions (Transaction, PipelineRun, ProcessedEmail)
    routes/
      transactions.py          Transaction CRUD, filtering, bulk update
      pipeline.py              Trigger runs, list runs, run status
      scraper.py               Scraper control + OAuth endpoints

  pipeline/                  # Classification pipeline (shared by both ingestion paths)
    config.py                  Configuration (models, pricing, paths, fallback chain)
    models.py                  Pydantic models & enums (ParsedTransaction, CanonicalTransaction)
    parsers/                   Statement file parsers (HDFC savings/CC, ICICI)
    transformer.py             ParsedTransaction -> CanonicalTransaction
    rules_classifier.py        Deterministic classification rules
    llm_classifier.py          LLM abstraction (multi-model fallback, caching, token tracking)
    db_writer.py               SQLite writer: content-hash dedup + email reconciliation

  scraper/                   # Gmail email scraper — see scraper/README.md
    README.md                  Setup, coverage, reconciliation design, API reference

  scripts/
    discover_emails.py         One-time email discovery + OAuth consent (first-run helper)
    migrate_db.py              Idempotent DB migration for pre-Phase-4 databases

  prompts/                   # Prompt templates (YAML, safe to commit)

  data/
    arth.db                  Production SQLite database (gitignored)
    gmail_credentials.json   GCP OAuth credentials (gitignored)
    gmail_token.json         OAuth token, auto-created on first run (gitignored)

  tests/
    test_db_and_api.py         DB operations + API endpoint tests
    test_email_parsers.py      Email parser unit tests against real HTML fixtures
    test_reconciliation.py     Reconciliation logic tests
    test_orchestrator.py       Orchestrator integration tests with mock Gmail
    fixtures/email_samples/    Real HTML email fixtures captured during discovery

  .env / .env.example        API keys + Gmail config (.env gitignored)
  requirements.txt           Python dependencies
```

## Real-Time Email Scraper

The server also scrapes HDFC and ICICI transaction alert emails every 15 minutes via the Gmail API, giving ~70-80% real-time visibility on daily spending. Monthly statement uploads reconcile the two sources automatically — no duplicates, no lost review work.

| Bank / Account      | What email captures                       | What needs a statement           |
| ------------------- | ----------------------------------------- | -------------------------------- |
| HDFC CC (1905/5778) | All CC swipes (real-time)                 | Refunds, cashback, auto-pay      |
| HDFC Savings 3703   | UPI outbound + inbound                    | Net banking transfers, salary    |
| ICICI Savings 6118  | IMPS + NEFT via iMobile (manual triggers) | All inbound, ICICI Direct trades |

Full setup instructions and design details: **[`scraper/README.md`](scraper/README.md)**
