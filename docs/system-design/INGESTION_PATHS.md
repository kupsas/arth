# Ingestion paths — declared hierarchy (DESKTOP_PREREQS item 7)

**Policy:** **Gmail / email scraping is the primary path** for ongoing ingestion. **File-based pipeline** (`pipeline.run`, API upload, `holding_pipeline` CLI) is the **explicit fallback** for gaps email cannot cover, historical migration, or recovery.

Reconciliation when two paths describe the same real-world transaction is implemented in [`pipeline/db_writer.py`](../../pipeline/db_writer.py). See [`scraper/README.md`](../../scraper/README.md#how-reconciliation-works).

---

## Bank transactions (`transactions` table)

| Source key | Primary (email) | Fallback (files) | Parser modules (email vs file) |
|------------|-----------------|------------------|--------------------------------|
| `hdfc_savings` | InstaAlerts + combined statement PDFs from Gmail | Yearly `.txt` under configured dirs via [`pipeline/config.py`](../../pipeline/config.py) `SOURCE_CONFIGS` | `scraper/email_parsers/hdfc_bank.py`, `hdfc_statement.py` vs `pipeline/parsers/hdfc_savings.py` |
| `hdfc_cc_*` | InstaAlerts + CC statement PDF emails | Monthly CSV dirs via `SOURCE_CONFIGS` | `hdfc_cc_statement.py` vs `pipeline/parsers/hdfc_cc.py` |
| `icici_savings` | InstaAlerts + monthly/annual statement PDFs | Yearly PDF dirs via `SOURCE_CONFIGS` | `icici_bank.py`, `icici_statement.py` vs `pipeline/parsers/icici_savings.py` |

**Operational note:** Use `run_historical_backfill` or [`scripts/scrape_historical.py`](../../scripts/scrape_historical.py) for multi-year Gmail sweeps instead of legacy per-bank backfill scripts.

---

## Holdings and investment transactions

| Asset / flow | Paths | Notes |
|--------------|-------|--------|
| **ICICI PPF** | Annual statement PDF (email → `icici_ppf_pdf.py`) vs CSV export (`icici_ppf.py`, `holding_pipeline --source icici_ppf`) | **Complementary:** annual PDF is authoritative for FY-shaped data in-mail; CSV is useful for bulk import when email is unavailable. Avoid overlapping periods without dedup awareness. |
| **ICICI Direct equity** | Full portfolio CSV (`icici_direct_equity`) vs NSE “Trades executed” PDF emails (`icici_direct_contract_note.py`) | **Complementary:** CSV for positions/cost snapshot; NSE PDFs for per-fill execution. Linking uses `investment_txn_linking`; avoid double-counting fills. |

---

## Entry points (quick reference)

| Mechanism | Where | Role |
|-----------|--------|------|
| Scheduled + manual scrape | `scraper/orchestrator.scrape_new_emails`, APScheduler, `POST /api/scraper/trigger` | Incremental email |
| Historical window | `run_historical_backfill`, `POST /api/scraper/backfill`, `scripts/scrape_historical.py` | Date-bounded Gmail import |
| File pipeline | `python -m pipeline.run`, `POST /api/pipeline/run`, `POST /api/pipeline/upload` | Statement files on disk / upload |
| Holdings / liabilities | `pipeline/holding_pipeline.py` CLI | Broker exports, PPF CSV, NPS, etc. |

---

## PDF passwords

Logical kinds and env-var chains are centralized in [`scraper/pdf_passwords.py`](../../scraper/pdf_passwords.py). Setup-wizard (DOB/PAN) derivation can plug in without new scattered `getenv` calls in each parser.
