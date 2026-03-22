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

## Authentication

The API uses an **httpOnly session cookie** (`arth_session`) after a successful login. Configure credentials via `.env`: `AUTH_USERNAME`, `AUTH_PASSWORD`, `AUTH_SECRET_KEY` (see `api/auth.py`).

| Area | Auth required? |
|------|----------------|
| `POST /api/auth/login`, `POST /api/auth/logout` | No (login issues the cookie; logout clears it) |
| `GET /health` | No |
| Everything under `/api/transactions`, `/api/metrics`, `/api/pipeline`, `/api/scraper`, `/api/recurring`, `/api/goals`, `/api/settings` | **Yes** — missing or invalid session → `401` |
| `GET /api/auth/me` | Yes — use this to check whether the browser still has a valid session |

The interactive **Swagger UI** at `/docs` cannot easily use cookie auth for try-it-out calls unless you log in from the dashboard (or another client) first and share the browser session. For day-to-day use, prefer the dashboard or `curl` with `-b`/`-c` cookie jars.

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
| `sort_by` | string | One of: `txn_date`, `amount`, `created_at`, `counterparty` |
| `sort_order` | string | `asc` or `desc` (default `desc` for date-oriented views) |

**`PATCH /api/transactions/{id}` mutable fields:** `counterparty`, `counterparty_category`, `txn_type`, `spend_category` (Need/Want/Saving/Investment), `notes`, `is_reviewed`, `exclude_from_analytics`, `exclusion_reason`

---

### Auth — `/api/auth`

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/login` | Body: `{ "username", "password" }` — sets `arth_session` cookie on success |
| `POST` | `/logout` | Clears session cookie |
| `GET` | `/me` | Returns `{ authenticated, username }` if session is valid |

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
| `GET` | `/negative-surplus-months` | Months where expenses exceeded income (deficit list + totals) | `months` (default 12, max 36) |
| `GET` | `/by-spend-category` | Aggregates by macro spend tag (Need / Want / Saving / Investment) | `date_from`, `date_to` (optional) |
| `GET` | `/goal-progress` | Progress snapshot for one goal (adherence, investment flows, etc.) | `goal_id` (required) |
| `GET` | `/investment-trend` | Month-by-month purchases, sales, net (investment flows) | `months` (default 6, max 36) |
| `GET` | `/expense-trend-stacked` | Need vs want stacked bars by month | `months` (default 6, max 36) |
| `GET` | `/category-trend` | Single-series trend (Swiggy, food, travel, etc.) | `series` (required), `months` |
| `GET` | `/top-expenses` | Largest transactions at or above a rupee threshold | `threshold` (default 5000), optional `year_month` (`YYYY-MM`) |
| `GET` | `/bar-drilldown` | Rows behind a dashboard bar segment | `chart` (required) + filters — see Swagger |

---

### Pipeline — `/api/pipeline`

| Method | Path | Description |
|---|---|---|
| `POST` | `/run` | Trigger a pipeline run |
| `GET` | `/runs` | List past pipeline runs |
| `GET` | `/runs/{id}` | Single run status (poll this after triggering a run) |
| `POST` | `/upload` | Upload a statement file and enqueue processing (dashboard “upload statement”) — see Swagger for multipart fields |

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
| `POST` | `/oauth/init` | Start OAuth2 flow — returns a browser URL to open |
| `GET` | `/oauth/status` | Check if Gmail token exists and is valid |
| `GET` | `/coverage` | Coverage map: which accounts have email alerts, which don't |

---

### Recurring — `/api/recurring`

Auto-detected recurring patterns (subscriptions, rent-like debits, etc.).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/detect` | Run detection over stored transactions (expensive — not every request) |
| `GET` | `/summary` | Counts: active / overdue / confirmed patterns |
| `GET` | `/` | List all patterns |
| `GET` | `/{pattern_id}` | One pattern |
| `PATCH` | `/{pattern_id}` | Update e.g. `is_confirmed`, `is_active` |

---

### Goals — `/api/goals`

User-defined goals (expense limits, savings targets, etc.). See `Goal` in `api/models.py` for `goal_type`, `status`, and `chart_key` semantics.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create a goal |
| `GET` | `/` | List goals (optional filters in query — see Swagger) |
| `GET` | `/{goal_id}` | One goal |
| `PATCH` | `/{goal_id}` | Update fields including `current_value`, `status`, `notes` |
| `DELETE` | `/{goal_id}` | Remove a goal |

---

### Settings — `/api/settings`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reminders` | List payment reminders (rent, CC due, etc.) |
| `POST` | `/reminders` | Create reminder |
| `PATCH` | `/reminders/{reminder_id}` | Update |
| `DELETE` | `/reminders/{reminder_id}` | Delete |

---

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}` |

---

## Database Schema

Six core domain tables (plus SQLModel metadata), all defined in `api/models.py`.

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
| `spend_category` | str\|null | Macro tag: `NEED`, `WANT`, `SAVING`, `INVESTMENT` (mainly outflows) |
| `exclude_from_analytics` | bool | When true, row is hidden from metrics but still listed in the table |
| `exclusion_reason` | str\|null | e.g. `refund`, `duplicate`, `test_transaction`, `other` |
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

### `recurring_patterns`

Stores auto-detected recurring debit/credit patterns (counterparty, frequency, next expected date, user confirmation flags). See `RecurringPattern` in `api/models.py`.

---

### `goals`

User goals (`goal_type`, targets, `chart_key` binding to dashboard charts, `progress_cadence`, manual `current_value` for non-auto goals). See `Goal` in `api/models.py`.

---

### `reminders`

Monthly payment reminders (`due_day_of_month`, optional amount and category). See `Reminder` in `api/models.py`.

---

## Architecture Notes

- **CORS:** Defaults to `localhost:3000` and `localhost:8000`. For Cloudflare Tunnel or other origins, set `CORS_EXTRA_ORIGINS` in `.env` (comma-separated full origins, e.g. `https://abc.trycloudflare.com`). `allow_credentials=True` so the session cookie works cross-port in dev.
- **Auth:** Cookie-based session for the two household accounts. Not a multi-tenant SaaS — treat `.env` secrets and network exposure accordingly if you ever host off localhost.
- **Scheduler lifecycle:** The APScheduler background thread starts with the FastAPI `lifespan` context and shuts down cleanly on exit. One `uvicorn` command manages the API and the email scraper.
- **Database sessions:** Injected via FastAPI's `Depends(get_session)`. No global session state — each request gets its own session.
- **DB init:** `init_db()` is called on every server start. It creates tables that don't exist and leaves existing ones alone — safe to run repeatedly.
