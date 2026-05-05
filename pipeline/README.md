# Pipeline

The Arth classification pipeline turns raw bank statements into fully-classified, deduplicated transactions stored in SQLite. It runs in 5 stages and is entirely bank-agnostic after Stage 1.

---

## How It Works

```
Raw Statement (CSV / PDF / TXT)
        │
   [1] Parse          → source-specific parser; extracts raw rows
        │
   [2] Transform      → normalize to canonical schema
                        (assign content_hash, ISO dates, direction, single amount)
        │
   [3] Rules Classify → deterministic rules fill channel, txn_type, upi_type
                        achieves 96–100% accuracy on structured narrations
        │
   [4] LLM Classify   → fills counterparty, counterparty_category,
                        and any remaining ambiguous fields
        │
   [5] Write SQLite   → content-hash dedup; backfills NULLs on re-runs
                        preserves all manual edits
```

**The rule:** Only what a parser knows about its own file format lives in `parsers/`. Everything downstream — the schema, classification logic, and DB writer — is completely bank-agnostic.

---

## CLI Reference

```bash
# Run a single source (default: hdfc_savings)
python3 -m pipeline.run --source hdfc_savings

# Run all 4 sources sequentially
python3 -m pipeline.run --all-sources

# Rules-only pass (no LLM, fast — good for testing)
python3 -m pipeline.run --all-sources --llm none

# Force a specific LLM model
python3 -m pipeline.run --source hdfc_savings --llm gemini-3.1-flash-lite

# Also run accuracy validation against the GSheet benchmark
python3 -m pipeline.run --source hdfc_savings --validate

# Legacy: write to CSV instead of SQLite
python3 -m pipeline.run --all-sources --csv
```

The pipeline can also be triggered via the API: `POST /api/pipeline/run`.

---

## Classification Strategy

### Stage 3: Deterministic Rules First

`rules_classifier.py` applies ordered pattern-matching rules to every transaction before the LLM is called. This layer handles everything that can be classified without ambiguity:

- **Channel detection:** UPI prefix → `UPI`, NEFT/IMPS/ACH → `BANK`, card narrations → `CARD`, broker → `BROKER`
- **UPI type detection:** P2P (person names, phone numbers) vs P2M (merchants, businesses, UPI handles)
- **Known transaction types:** Self-transfers, salary credits, CC bill payments, investment flows, rent
- **Known merchants:** High-frequency counterparties resolved by keyword, bypassing LLM entirely

**Rule ordering is critical.** NEFT-routed broker transactions (Quant MF, NSDL) must be detected by counterparty keyword *before* the generic `NEFT-` prefix check, or they get misclassified as `Channel.BANK`. When adding new rules, always check what they might shadow.

Accuracy after rules only (no LLM):

| Source | channel | txn_type | counterparty | counterparty_category |
|---|---|---|---|---|
| Sample credit card A | 100% | 100% | 89.7% | 97.4% |
| Sample credit card B | 100% | 100% | 100% | 100% |
| Sample savings account | 100% | 96.2% | 97.2% | 97.2% |

### Stage 4: LLM for the Long Tail

`llm_classifier.py` handles only what rules couldn't resolve — primarily ambiguous counterparty names and one-off merchants. It uses a **multi-model fallback chain**:

1. `gemini-3.1-flash-lite` — primary (81% accuracy, $0.0025/20 txns)
2. `gemini-2.5-flash` — backup (same provider, slightly costlier)
3. `claude-haiku-4-5` — backup (Anthropic — different provider)
4. `gpt-5-mini` — backup (OpenAI — different provider)

Set `LLM_MODEL=auto` in `.env` to use the full chain. Set `LLM_MODEL=none` to skip LLM entirely (rules-only mode).

**All LLM responses are cached** by batch content hash (model-independent). Re-running the pipeline on the same statement only calls the LLM for genuinely new transactions.

Benchmark methodology and full results: [`docs/evaluations/llm-benchmark-2026-03/README.md`](../docs/evaluations/llm-benchmark-2026-03/README.md)

### Final accuracy (rules + LLM, large real-world savings sample, March 2026)

| Field | Accuracy |
|---|---|
| direction / amount / channel | 100% |
| txn_type | 98.7% |
| upi_type | 98.1% |
| counterparty | 94.9% |
| counterparty_category | 93.7% |

---

## How to Add a New Bank Source

Adding a new bank requires exactly **one new file** and **two small config changes**.

### Step 1 — Write the parser

Create `pipeline/parsers/newbank.py`. The parser must extend `BaseParser` and implement `parse()`:

```python
from pipeline.parsers.base import BaseParser
from pipeline.models import ParsedTransaction

class NewBankSavingsParser(BaseParser):
    def parse(self, source_path) -> list[ParsedTransaction]:
        # Read the file, extract rows, return ParsedTransaction objects.
        # This is the ONLY place where file-format knowledge lives.
        ...
```

`ParsedTransaction` is the raw, bank-specific representation. The transformer in Stage 2 converts it to `CanonicalTransaction`. You never touch the transformer.

**PDF parsing note:** For PDF sources, use `pdfplumber`. The ICICI PDF required word-level coordinate extraction with a midpoint-split heuristic for multi-line descriptions — `extract_tables()` silently returned only the header row. Document your approach in a comment.

### Step 2 — Register the parser

In `pipeline/parsers/__init__.py`, add to `PARSER_REGISTRY`:

```python
from pipeline.parsers.newbank import NewBankSavingsParser

PARSER_REGISTRY = {
    ...
    "new_bank_savings": NewBankSavingsParser,
}
```

### Step 3 — Add source config (per user in SQLite)

Insert a row into the `user_pipeline_sources` table for your `user_id` (same string as `ARTH_USER_ID` / dashboard login): `source_key`, `account_id`, `currency` (default `INR`), and `statement_folder` (subdirectory name under `docs/personal-data/`). The CLI and API load this via `pipeline.config.get_source_configs(user_id, session)`.

You can seed the legacy snapshot with:

```bash
python3 scripts/migrate_sashank_config_to_db.py --user-id yourname
```

(`migrate_sashank_config_to_db.py` is a legacy filename — it seeds **any** `user_id` you pass.)

Or insert manually (example shape):

```sql
INSERT INTO user_pipeline_sources (user_id, source_key, account_id, currency, statement_folder)
VALUES ('yourname', 'new_bank_savings', 'NEWBANK_SAV_XXXX', 'INR', 'NewBank_Savings');
```

### Step 4 — Run it

```bash
export ARTH_USER_ID=yourname
# Test with rules only first (fast, no LLM cost)
python3 -m pipeline.run --source new_bank_savings --llm none

# Full run with LLM
python3 -m pipeline.run --source new_bank_savings

# Validate against GSheet benchmark (if you have ground-truth rows)
python3 -m pipeline.run --source new_bank_savings --validate
```

---

## Key Files

| File | What it does |
|---|---|
| `config.py` | Paths, LLM fallback chain, model pricing, cache dir; per-user statement sources in SQLite (`get_source_configs`) |
| `models.py` | Pydantic models (`ParsedTransaction`, `CanonicalTransaction`) and all classification enums (`TxnType`, `Channel`, `UPIType`, `CounterpartyCategory`) |
| `parsers/base.py` | `BaseParser` abstract class |
| `parsers/hdfc_savings.py` | HDFC savings account (.txt format, `\t`-separated) |
| `parsers/hdfc_cc.py` | HDFC credit card (CSV format, handles both 1905 and 5778) |
| `parsers/icici_savings.py` | ICICI savings account (PDF format, word-coordinate extraction) |
| `holding_parsers/` | ICICI Direct, NPS, and other **position** parsers — not the same as cash `parsers/` |
| `holding_pipeline.py` | Wires holding ingest / enrichment into the DB (see `api` + `pipeline` integration) |
| `transformer.py` | `ParsedTransaction` → `CanonicalTransaction`: assigns content_hash, ISO dates, direction, amount |
| `rules_classifier.py` | Deterministic classification rules (channel, txn_type, upi_type, known merchants) |
| `llm_classifier.py` | LLM abstraction: multi-model fallback, response caching, token tracking |
| `db_writer.py` | SQLite writer: content-hash dedup, NULL backfill, email reconciliation |
| `run.py` | CLI entry point (`python3 -m pipeline.run`) |
| `validator.py` | Accuracy measurement against the GSheet ground-truth benchmark |
| `prompts.py` | Thin loader: reads YAML prompt files and interpolates runtime variables |

---

## Dedup and Idempotency

Every transaction is identified by a `content_hash` — a SHA-256 digest of `(txn_date, raw_description, amount, account_id)`.

- **On insert:** If the hash already exists in the DB, skip the row (it's a duplicate).
- **On re-run:** If a field is NULL in the DB and the pipeline now has a value for it, backfill it — without overwriting any manually-corrected values.

This means running the pipeline on the same statement file twice is completely safe. It also means adding new classification rules and re-running will fill in fields that were previously NULL — you get retroactive improvement without touching existing data.

---

## Email Reconciliation

When a statement is uploaded for an account that also has email-sourced rows, `db_writer.py` fuzzy-matches each incoming statement row against existing email rows (same account, exact amount, date ±1 day). On match, the email row is upgraded with richer statement data (`source_type` → `"reconciled"`), and any manual edits the user made are preserved.

This means one row per real-world transaction, always — regardless of which path it arrived through first.

See [`scraper/README.md`](../scraper/README.md) for the full reconciliation design.
