# Scripts

One-time setup and utility scripts. These are not part of the main pipeline or API — they're tools for first-run setup, database migration, and benchmark preparation.

---

## `backfill_price_history.py`

**When to use:** You want roughly **one year** of daily closes/NAVs in the `prices` table for every **current** market-priced holding (equities/ESOP/SGB/gold ETFs via NSE bhavcopy; mutual funds via **AMFI’s official** [NAV history download](https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx) in chunked date ranges, with optional [mfapi.in](https://api.mfapi.in/) fallback per scheme). The normal API startup path only backfills NSE **forward from the last stored date** (or a capped window when empty), so it will not deepen history if you already have a few months of data.

**Prerequisites (data quality):**

- **NSE:** `holdings.symbol` must match the **exact** NSE ticker in bhav (`TckrSymb`), e.g. `LT` not `LARTOU`.
- **Mutual funds:** `holdings.symbol` must be the **AMFI scheme code** (digits). Empty MF symbols are skipped.
- **NPS / other sleeves** outside market-priced equity/MF/SGB/gold ETF paths are unchanged (see `api/services/price_feed.py`).

**Runbook (test DB first, then prod):**

1. **Backup prod** if you will touch it: `scripts/backup_db.sh` or copy `data/arth.db`.
2. **Test DB:** `APP_ENV=test python3 scripts/backfill_price_history.py --days 365`
3. Inspect: `SELECT symbol, COUNT(*), MIN(date), MAX(date) FROM prices GROUP BY symbol;`
4. **Prod:** `python3 scripts/backfill_price_history.py --days 365` (or `APP_ENV=prod` explicitly).

**Options:**

- `--dry-run` — print symbol lists, date range, and estimated weekday count; no network or DB writes.
- `--mf-only` — skip NSE bhav; only AMFI portal (and mfapi fallback) for mutual funds — use after a full backfill when you only need MF history without re-crawling NSE.
- `--user-id` — limit which holdings are considered.
- `--buffer-days` — extra calendar days before the `--days` window for NSE weekends/holidays (default 14).

**Runtime:** NSE walks **one bhav file per weekday** per symbol (throttled). A full year across many symbols can take **tens of minutes**. MF history uses **AMFI portal** downloads (one large text file per **date chunk** for *all* schemes, not per scheme), so a ~1y window is several chunked requests plus parsing. International Yahoo tickers (`GC=F`, etc.) are **not** included here — refresh-only in the API.

**AMFI scheme codes:** ICICI Direct PDF/CSV statements usually show **folio** and **scheme name**, not the numeric AMFI code. Match the **exact** plan name (Regular vs Direct, Growth vs IDCW) against AMFI’s published `NAVAll.txt` or your AMC factsheet — wrong code = wrong NAV series.

**Avoid fetching twice (test → prod):** Run the backfill on `data/arth_test.db` first (`APP_ENV=test`). After you like the `prices` counts, copy rows into prod with **`merge_prices_from_db.py`** (upserts on `symbol`+`date` only — no other tables). Then run `refresh_all_prices` once on prod if you want holding marks updated from the latest row.

---

## `merge_prices_from_db.py`

**When to use:** You already backfilled `prices` on `arth_test.db` and want the same rows in `arth.db` **without** another NSE/AMFI history crawl.

```bash
python3 scripts/merge_prices_from_db.py --source data/arth_test.db --into data/arth.db --dry-run
python3 scripts/merge_prices_from_db.py --source data/arth_test.db --into data/arth.db
```

**Caveat:** Prod holdings must use the **same** `symbol` strings (NSE tickers, AMFI codes) you used when backfilling test; otherwise you will have price rows the UI never looks up. **Backup prod** before merging.

**Implementation note:** The script uses SQLModel + ``upsert_prices`` (not raw SQL ``ON CONFLICT``) so it works with older system SQLite builds that lack UPSERT.

---

## `discover_emails.py`

**When to use:** First-time Gmail API setup, or to explore what bank email senders and subjects exist in your inbox before writing a new email parser.

**What it does:**
1. Runs the OAuth2 browser consent flow (creates `data/gmail_token.json`)
2. Searches your Gmail inbox for known bank alert senders
3. Prints a breakdown of email subjects and counts — useful for confirming which email formats exist before committing to a parser

```bash
python3 scripts/discover_emails.py
```

> This is the "pre-server" OAuth path. If the API server is already running, you can also use `GET /api/scraper/oauth/init` to get a browser URL and complete OAuth without stopping the server.

**Output example:**
```
Sender: alerts@hdfcbank.net
  "debited via Credit Card"   →  47 emails
  "UPI txn"                   →  112 emails
  ...
```

This output is what you use to write the `can_parse(sender, subject)` method of a new email parser.

---

## `migrate_db.py`

**When to use:** If you have a database created before Phase 4 (the email scraper) and want to upgrade its schema without losing data.

**What it does:** Idempotently adds the Phase 4 columns and tables to an existing database:
- Adds `source_type` column to `transactions` (defaults to `"statement"` for all existing rows)
- Adds `gmail_message_id` column to `transactions` (defaults to `NULL`)
- Creates the `processed_emails` table if it doesn't exist

```bash
python3 scripts/migrate_db.py
```

This script is **idempotent** — safe to run multiple times on a database that has already been migrated. It checks for column/table existence before making changes.

> **Not needed for new databases.** If you're starting fresh (running `init_db()` after Phase 4 was merged), the schema is created correctly from the start.

---

## `export_benchmark.py`

**When to use:** When refreshing the LLM benchmark test fixture with new ground-truth examples.

**What it does:** Samples transactions from the GSheet ground-truth CSV and exports them in the JSON format expected by the benchmark runner (`benchmark_20.json`). The export focuses on the hard-to-classify cases — the ones that actually differentiate models.

```bash
python3 scripts/export_benchmark.py
```

Output goes to `docs/evaluations/llm-benchmark-2026-03/benchmark_20.json`.

After exporting, run the benchmark to see how the current prompt and model stack performs:
```bash
python3 docs/evaluations/llm-benchmark-2026-03/benchmark.py
```
