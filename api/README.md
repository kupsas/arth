# API

FastAPI backend for Arth. Serves transaction data, aggregated metrics, pipeline controls, and email scraper management. SQLite database via SQLModel.

---

## Running the Server

```bash
# From the repo root
python3 -m uvicorn api.main:app --port 8000 --reload
```

> Use `python3 -m uvicorn`, not the bare `uvicorn` binary — the global binary may point to a different Python than your SQLModel install.

**Interactive docs (Swagger UI):** http://localhost:8000/docs

On startup, the server:
1. Initializes the SQLite database (creates tables if they don't exist — idempotent, safe every boot)
2. Starts the Gmail email scraper scheduler (pauses automatically if OAuth hasn't been set up yet)

---

## Environments

| Environment | DB file | Start command |
|---|---|---|
| prod | `data/arth.db` | `python3 -m uvicorn api.main:app --port 8000` |
| test | `data/arth_test.db` | `APP_ENV=test python3 -m uvicorn api.main:app --port 8001` |
| pytest | in-memory SQLite | `pytest tests/` |

---

## Endpoints

### Transactions — `/api/transactions`

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | List transactions with filters and pagination |
| `GET` | `/{id}` | Single transaction by ID |
| `PATCH` | `/{id}` | Update mutable fields on one transaction |
| `PATCH` | `/bulk` | Bulk update (e.g. mark multiple as reviewed) |

**`GET /api/transactions` query params:**

| Param | Type | Description |
|---|---|---|
| `date_from` | date | Start date (inclusive) |
| `date_to` | date | End date (inclusive) |
| `account_id` | string | Filter by account (e.g. `HDFC_SAL_3703`) |
| `direction` | string | `INFLOW` or `OUTFLOW` |
| `category` | string | Filter by `counterparty_category` |
| `txn_type` | string | Filter by `txn_type` |
| `is_reviewed` | bool | Filter by review status |
| `search` | string | Full-text search on `raw_description` and `counterparty` |
| `page` | int | Page number (default 1) |
| `page_size` | int | Rows per page (default 50, max 200) |
| `sort_by` | string | Column to sort by; prefix with `-` for descending (e.g. `-txn_date`) |

**`PATCH /api/transactions/{id}` mutable fields:** `counterparty`, `counterparty_category`, `txn_type`, `notes`, `is_reviewed`

---

### Metrics — `/api/metrics`

All metrics endpoints accept `date_from` and `date_to` query params (both default to the current calendar month).

**Double-counting note:** `CARD_PAYMENT` (paying the CC bill from savings) and `SELF_TRANSFER` (moving money between own accounts) are excluded from all expense totals. Individual CC swipes are captured as `CARD_EXPENSE` on the credit card statement — counting both would double-count every credit card purchase.

| Method | Path | Description | Key params |
|---|---|---|---|
| `GET` | `/summary` | Total income, expense, total_savings (Asset Markets outflows), net, savings rate, txn count | `date_from`, `date_to` |
| `GET` | `/by-category` | Spending or income ranked by category with percentages | `date_from`, `date_to`, `direction` |
| `GET` | `/top-counterparties` | Top N merchants by spend (OUTFLOW only) | `date_from`, `date_to`, `limit` |
| `GET` | `/monthly-trend` | Month-by-month income vs expense, zero-filled for empty months | `months` (default 12, max 36) |
| `GET` | `/accounts-summary` | Per-account inflow/outflow totals across all time | — |

---

### Pipeline — `/api/pipeline`

| Method | Path | Description |
|---|---|---|
| `POST` | `/run` | Trigger a pipeline run |
| `GET` | `/runs` | List past pipeline runs |
| `GET` | `/runs/{id}` | Single run status (poll this after triggering a run) |

**`POST /api/pipeline/run` body:**
```json
{
  "source_key": "hdfc_savings",
  "llm_model": "auto"
}
```
`source_key` accepts any key from `SOURCE_CONFIGS` in `pipeline/config.py`, or `"all"` to run all sources sequentially.

---

### Scraper — `/api/scraper`

See [`scraper/README.md`](../scraper/README.md) for the full scraper reference including first-time setup.

| Method | Path | Description |
|---|---|---|
| `GET` | `/status` | Scheduler state: running/paused, last run time, next run time, counts |
| `POST` | `/trigger` | Run one scrape cycle immediately (async — returns result when done) |
| `POST` | `/start` | Resume the scheduler |
| `POST` | `/stop` | Pause the scheduler |
| `PATCH` | `/config` | Update poll interval in minutes |
| `GET` | `/emails` | List processed emails — paginated, filterable by status/sender |
| `GET` | `/oauth/init` | Start OAuth2 flow — returns a browser URL to open |
| `GET` | `/oauth/status` | Check if Gmail token exists and is valid |
| `GET` | `/coverage` | Coverage map: which accounts have email alerts, which don't |

---

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}` |

---

## Database Schema

Three tables, all defined in `api/models.py` using SQLModel.

### `transactions`

The core financial data table. One row = one real-world economic event.

| Column | Type | Description |
|---|---|---|
| `id` | int | Primary key |
| `content_hash` | str | SHA-256 of `(txn_date, raw_description, amount, account_id)` — dedup key |
| `txn_date` | date | Transaction date |
| `account_id` | str | e.g. `HDFC_SAL_3703`, `HDFC_CC_1905` |
| `source_statement` | str | Which statement file this came from |
| `direction` | str | `INFLOW` or `OUTFLOW` |
| `amount` | float | Always positive |
| `currency` | str | Default `INR` |
| `txn_type` | str\|null | e.g. `UPI_EXPENSE`, `SALARY`, `CARD_EXPENSE`, `SELF_TRANSFER` |
| `channel` | str\|null | `UPI`, `BANK`, `CARD`, `BROKER`, `UPI_LITE` |
| `upi_type` | str\|null | `P2P`, `P2M`, `LITE_SELF_FUND`, or `NA` |
| `counterparty` | str\|null | Normalized merchant or person name |
| `counterparty_category` | str\|null | e.g. `Food & Dining`, `Shopping & E-commerce` |
| `raw_description` | str | Exact narration from bank statement — never edited |
| `ref_number` | str\|null | Cheque or UPI reference number |
| `closing_balance` | float\|null | Account balance after this transaction |
| `notes` | str\|null | Manual notes |
| `is_reviewed` | bool | Whether a human has reviewed/approved this transaction |
| `source_type` | str | `"statement"`, `"email"`, or `"reconciled"` |
| `gmail_message_id` | str\|null | Source Gmail message ID (email and reconciled rows only) |
| `pipeline_run_id` | int\|null | FK → `pipeline_runs.id` |
| `created_at` / `updated_at` | datetime | Timestamps |

**`source_type` values:**

| Value | Meaning |
|---|---|
| `"statement"` | Inserted by the file-based pipeline — the default |
| `"email"` | Inserted by the Gmail scraper; `is_reviewed=False` until statement arrives |
| `"reconciled"` | Started as `"email"`, upgraded when the matching statement row arrived |

---

### `pipeline_runs`

Audit trail of each pipeline execution.

| Column | Description |
|---|---|
| `id` | Primary key |
| `source_key` | e.g. `hdfc_savings` or `all` |
| `llm_model` | Model used (`auto`, `none`, or a specific model name) |
| `txn_count` | Total rows processed |
| `new_count` | Rows actually inserted (non-duplicates) |
| `updated_count` | Rows that had NULLs backfilled |
| `status` | `running`, `completed`, or `failed` |
| `txn_date_min` / `txn_date_max` | Date range of transactions in this run |
| `started_at` / `completed_at` | Timestamps |
| `error_message` | Populated on `failed` |

---

### `processed_emails`

Dedup ledger for the Gmail scraper. One row per Gmail message the scraper has touched — prevents re-processing on server restarts.

| Column | Description |
|---|---|
| `gmail_message_id` | Unique Gmail message ID |
| `sender` | Normalized from-address |
| `subject` | Email subject |
| `received_at` | Email timestamp |
| `txn_count` | How many transactions were created from this email |
| `status` | `"processed"`, `"skipped"`, or `"failed"` |
| `error_message` | Populated on `"failed"` |

---

## Architecture Notes

- **CORS:** Defaults to `localhost:3000` and `localhost:8000`. For Cloudflare Tunnel or other origins, set `CORS_EXTRA_ORIGINS` in `.env` (comma-separated full origins, e.g. `https://abc.trycloudflare.com`).
- **Auth:** No authentication in the current implementation. This is a known gap — see the gap analysis for the security roadmap.
- **Scheduler lifecycle:** The APScheduler background thread starts with the FastAPI `lifespan` context and shuts down cleanly on exit. One `uvicorn` command manages the API and the email scraper.
- **Database sessions:** Injected via FastAPI's `Depends(get_session)`. No global session state — each request gets its own session.
- **DB init:** `init_db()` is called on every server start. It creates tables that don't exist and leaves existing ones alone — safe to run repeatedly.
