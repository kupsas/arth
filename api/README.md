# API

FastAPI backend for Arth. Serves transactions, metrics, portfolio (holdings, prices, investment activity), goals (including hierarchy and simulation helpers), pipeline controls, and email scraper management. SQLite database via SQLModel.

---

## Running the Server

```bash
# From the repo root
python3 -m uvicorn api.main:app --port 8000 --reload --no-access-log
```

> Use `python3 -m uvicorn`, not the bare `uvicorn` binary — the global binary may point to a different Python than your SQLModel install.

`--no-access-log` hides one line per HTTP request (`GET /api/... 200`). Omit the flag when you need to debug which endpoint the dashboard is calling.

**Interactive docs (Swagger UI):** http://localhost:8000/docs

### Logs and terminals

Everything that uses the shared Python `logging` setup writes **the same format** to two places (see `pipeline/logging_config.py`):

| Destination | What you see | Typical level |
|-------------|----------------|---------------|
| **This terminal (stdout)** | Timestamped lines from Arth code (API startup, scraper summaries, price jobs, pipeline stages when triggered via API, errors). | INFO and above |
| **`data/logs/arth.log`** | Same lines **plus** DEBUG detail (per-email scraper steps, LLM batch internals, Gmail query strings). File rotates at ~5 MB (a few backups kept). | DEBUG and above |

**Uvicorn** adds its own startup banner. With `--no-access-log`, you will not see a line for every browser/API request — that noise is normal web-server traffic, not “something wrong.”

**Next.js dashboard terminal** (`npm run dev`) shows the dev server (compile, Fast Refresh). The dashboard app code does not spam `console.log` by design; if an API call fails, use the **browser** DevTools → Network tab to see the failing URL and status code.

**Local data hygiene:** keep manual SQLite copies under `data/backups/` (gitignored) rather than scattering `arth.db.bak-*` in `data/`. The whole `data/output/` tree is gitignored — delete stale exports (e.g. old pipeline CSVs) locally whenever you like.

On startup, the server:
1. Initializes the SQLite database (creates tables if they don't exist — idempotent, safe every boot)
2. Starts the Gmail email scraper scheduler (pauses automatically if OAuth hasn't been set up yet)
3. Kicks off **background** jobs (non-blocking): refresh stale **prices** (NSE / AMFI / yfinance as needed) and sync **IMF India CPI** history for inflation features (unless `INFLATION_DISABLE_IMF` is set)

See `api/main.py` `lifespan` for the exact behavior.

---

## Authentication

The API uses an **httpOnly session cookie** (`arth_session`) after a successful login. Configure credentials via `.env`: `AUTH_USERNAME`, `AUTH_PASSWORD`, `AUTH_SECRET_KEY` (see `api/auth.py`).

| Area | Auth required? |
|------|----------------|
| `POST /api/auth/login`, `POST /api/auth/logout` | No (login issues the cookie; logout clears it) |
| `GET /health` | No |
| Everything under `/api/*` **except** the two routes above | **Yes** — routers are mounted with `Depends(get_current_user)`; missing or invalid session → `401` |
| `GET /api/auth/me` | Yes — use this to check whether the browser still has a valid session |

That includes all of: transactions, metrics, pipeline, scraper, recurring, surplus, liquidity, goal-suggestions, inflation, simulate, goals (CRUD + tree), goal-links, life-events, settings, holdings, investment-transactions, liabilities, and prices.

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
| `account_id` | string | Filter by account (e.g. `YOUR_BANK_SAV`) |
| `direction` | string | `INFLOW` or `OUTFLOW` |
| `category` | string | Filter by category label |
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

**Double-counting note:** paying your credit card bill from savings and moving money between your own accounts are excluded from expense totals so you don't double-count card purchases.

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
`source_key` accepts any key present in `user_pipeline_sources` for the logged-in user, or `"all"` to run all configured sources sequentially.

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

User-defined goals (expense limits, savings targets, hierarchy links, etc.). See `Goal`, `GoalLink`, and related types in `api/models.py`.

**CRUD**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create a goal |
| `GET` | `/` | List goals (optional filters in query — see Swagger) |
| `GET` | `/priorities` | Priority scoring / ordering helpers |
| `POST` | `/reorder` | Reorder goals |
| `POST` | `/{goal_id}/decompose` | Decomposition helpers |
| `GET` | `/{goal_id}` | One goal |
| `PATCH` | `/{goal_id}` | Update fields including `current_value`, `status`, `notes` |
| `DELETE` | `/{goal_id}` | Remove a goal |

**Hierarchy & graph** (same `/api/goals` prefix; see Swagger for response shapes)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tree` | Goal tree structure |
| `GET` | `/allocation` | Allocation view |
| `GET` | `/{goal_id}/ancestors` | Ancestors in the hierarchy |
| `GET` | `/{goal_id}/descendants` | Descendants |
| `GET` | `/{goal_id}/impact` | Impact analysis for a goal |

---

### Goal links — `/api/goal-links`

Edges between goals (`GoalLink` model) — dependencies, ordering, or relationships in the hierarchy.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Create a link |
| `GET` | `/` | List links |
| `PATCH` | `/{link_id}` | Update |
| `DELETE` | `/{link_id}` | Delete |

---

### Life events — `/api/life-events`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List life events |
| `POST` | `/` | Create |
| `PATCH` | `/{event_id}` | Update |

---

### Surplus — `/api/surplus`

Household surplus calculations for goals / simulation (see services under `api/services/`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Current surplus snapshot |
| `GET` | `/monthly` | Month-by-month series |

---

### Liquidity — `/api/liquidity`

Holding liquidity and match-to-goal helpers.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/summary` | Aggregated liquidity view |
| `POST` | `/refresh` | Recompute / refresh |
| `GET` | `/goal-match/{goal_id}` | Match holdings to a goal |
| `GET` | `/goal-suggestions` | Starting-balance style suggestions |
| `POST` | `/mismatch-check` | Detect mismatches |

---

### Goal suggestions — `/api/goal-suggestions`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Suggested goal actions / hints (see Swagger) |

---

### Inflation — `/api/inflation`

IMF CPI–backed inflation series used by simulation and goals.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Current inflation inputs |
| `GET` | `/history` | Historical series |
| `POST` | `/refresh` | Pull / refresh from IMF |

---

### Simulation — `/api/simulate`

Goal funding and “what-if” simulation (surplus allocation, compare scenarios).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Run a simulation |
| `POST` | `/compare` | Compare scenarios |
| `POST` | `/allocate` | Allocation run |
| `POST` | `/from-current` | Simulate from current balances |

---

### Holdings — `/api/holdings`

Portfolio positions, net-worth history, returns, enrichment, and CSV import. See `Holding` in `api/models.py`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List holdings |
| `GET` | `/summary` | Summary cards / totals |
| `GET` | `/history` | Net worth history |
| `GET` | `/batch-returns` | Returns across holdings |
| `GET` | `/portfolio-value-trend` | Portfolio value trend series |
| `POST` | `/enrich` | Enrich holdings from pipeline / market data |
| `POST` | `/import` | Import holdings (CSV) |
| `GET` | `/{holding_id}` | Detail |
| `POST` | `/` | Create |
| `PATCH` | `/{holding_id}` | Update |

---

### Investment transactions — `/api/investment-transactions`

Broker / MF / PPF-style activity rows (separate from `transactions` cash ledger).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Paginated list |
| `PATCH` | `/bulk` | Bulk update |
| `PATCH` | `/{inv_id}` | Update one |
| `POST` | `/` | Create |
| `POST` | `/import` | Import batch |

---

### Liabilities — `/api/liabilities`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List |
| `GET` | `/summary` | Summary |
| `GET` | `/{liability_id}` | One liability |
| `POST` | `/` | Create |
| `PATCH` | `/{liability_id}` | Update |
| `DELETE` | `/{liability_id}` | Delete |

---

### Prices — `/api/prices`

Daily prices / NAV history keyed by symbol (NSE ticker, AMFI code, etc.).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/refresh` | Refresh prices for symbols in scope |
| `GET` | `/{symbol}/history` | Time series for one symbol |

---

### Settings — `/api/settings`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reminders` | List payment reminders (rent, CC due, etc.) |
| `POST` | `/reminders` | Create reminder |
| `PATCH` | `/reminders/{reminder_id}` | Update |
| `DELETE` | `/reminders/{reminder_id}` | Delete |
| `POST` | `/reminders/derive-anchors` | Derive reminder anchors from transactions |
| `GET` | `/reminders/status` | Status for reminder subsystem |

---

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}` |

---

## Database Schema

All tables are defined in `api/models.py`. The **cash ledger** is `transactions`; **portfolio** adds `holdings`, `investment_transactions`, `holding_value_snapshots`, `prices`, `liabilities`; **goals** add `goals`, `goal_links`, `life_events`, `reminders`, `recurring_patterns`, `inflation_rates`; **ops** include `pipeline_runs`, `processed_emails`.

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

### Portfolio & goals (abbreviated)

| Table | Purpose |
|-------|---------|
| `holdings` | Positions (equity, MF, PPF, NPS, etc.) |
| `investment_transactions` | Parsed investment / broker activity |
| `holding_value_snapshots` | Point-in-time valuations |
| `prices` | Daily close / NAV per symbol |
| `liabilities` | Loans and other liabilities |
| `goals` / `goal_links` | Goals and graph edges |
| `life_events` | Dated life events tied to planning |
| `inflation_rates` | CPI / inflation series used in simulation |

For column-level detail, use the SQLModel definitions in `api/models.py` or Swagger response schemas.

---

## Architecture Notes

- **CORS:** Defaults to `localhost:3000` and `localhost:8000`. For Cloudflare Tunnel or other origins, set `CORS_EXTRA_ORIGINS` in `.env` (comma-separated full origins, e.g. `https://abc.trycloudflare.com`). `allow_credentials=True` so the session cookie works cross-port in dev.
- **Auth:** Cookie-based session for the two household accounts. Not a multi-tenant SaaS — treat `.env` secrets and network exposure accordingly if you ever host off localhost.
- **Scheduler lifecycle:** The APScheduler background thread starts with the FastAPI `lifespan` context and shuts down cleanly on exit. One `uvicorn` command manages the API, scheduled Gmail polling, daily price refresh, weekly inflation sync, and weekly NSE reference + holdings enrichment (see `scraper/scheduler.py`).
- **Database sessions:** Injected via FastAPI's `Depends(get_session)`. No global session state — each request gets its own session.
- **DB init:** `init_db()` is called on every server start. It creates tables that don't exist and leaves existing ones alone — safe to run repeatedly.
