# Imports & sorting (`pipeline/`)

**What this folder does:** Turns bank **statement exports** into saved rows in your local money diary — with **sorting rules** doing the heavy lifting and optional **smart labels** (your API keys) catching the fuzzy bits.

If you’re day‑to‑day using Arth, you’ll mostly live in **onboarding + Settings**. You open this doc when you’re **adding a bank**, **running bulk imports from the terminal**, or **debugging a weird row**.

---

## The journey (plain English)

1. **Read the file** — Each bank has its own reader; only that piece knows CSV vs PDF quirks.
2. **Put rows in one shape** — Dates, amounts, and “money in vs out” line up the same for everyone after this step.
3. **Sorting rules** — Pattern-style logic fills channel (UPI, card, bank transfer…), transaction type, and UPI flavour where we can do it deterministically.
4. **Smart labels (optional)** — For merchants and categories still ambiguous, we ask an AI provider **you** configure — see `prompts/` for the templates (nothing secret there).
5. **Save** — Same real-world line never lands twice; re‑runs only fill empty cells — **your corrections stay put.**

Bank-specific knowledge stays in `**parsers/`** (cash) and `**holding_parsers/**` (investments). Everything after step 1 is shared across banks.

---

## Commands worth knowing

```bash
# One configured source (defaults depend on your setup)
python3 -m pipeline.run --source hdfc_savings

# Everything configured for your user, one after another
python3 -m pipeline.run --all-sources

# Fast dry pass — rules only, no smart-label API calls
python3 -m pipeline.run --all-sources --llm none

# Pick a specific model for smart labels (when not using “auto” chain)
python3 -m pipeline.run --source hdfc_savings --llm gemini-3.1-flash-lite

# Accuracy check vs the maintainer benchmark fixture (when you have it locally)
python3 -m pipeline.run --source hdfc_savings --validate
```

The **website** can also kick off runs — under the hood it hits the server’s pipeline routes (see **Swagger** at `/docs` when Arth’s server is running).

---

## Accuracy (big picture)

Rules alone already nail most **structured** lines. With smart labels on, representative savings samples land in the mid‑90s % on merchant/category-style fields — direction, amount, and channel at essentially **100%** on those samples. Full methodology and tables: `[docs/evaluations/llm-benchmark-2026-03/README.md](../docs/evaluations/llm-benchmark-2026-03/README.md)`.

---

## Adding support for a **new** bank

Rough recipe — still technical, but only contributors need it:

### 1. Write the reader

Create `pipeline/parsers/newbank.py` extending `BaseParser` — turn **their** file into `ParsedTransaction` rows. PDFs usually use `pdfplumber`; if tables look empty, you may need tighter layout logic (the ICICI PDF was picky — worth a short comment in code when you hack it).

### 2. Register it

Add the class to `PARSER_REGISTRY` in `pipeline/parsers/__init__.py`.

### 3. Tell Arth where files live

Per-user rows live in `**user_pipeline_sources`** (via onboarding / DB). Legacy helper if you’re migrating configs:

```bash
python3 scripts/migrate_sashank_config_to_db.py --user-id yourname
```

### 4. Run & iterate

```bash
export ARTH_USER_ID=yourname
python3 -m pipeline.run --source new_bank_savings --llm none   # cheap first pass
python3 -m pipeline.run --source new_bank_savings              # full pass
```

---

## When the **same** spend shows up twice (mail vs statement)

Alerts sometimes arrive **before** the monthly PDF. We **upgrade** the mail-backed row when the statement line matches — same account, same amount, date within a day — instead of inserting a duplicate. Your edits survive. Full story: `[scraper/README.md](../scraper/README.md)` (same matching rules in `db_writer.py`).

---

## Dedup & safe re-runs

Each row gets a stable fingerprint from date + description + amount + account. Seen before → skipped. Empty field later filled by better rules → **backfilled**, not overwritten if you already fixed it.

---

## Where to read the code


| Piece                 | Role                                       |
| --------------------- | ------------------------------------------ |
| `run.py`              | CLI entry                                  |
| `config.py`           | Paths, models, per-user source definitions |
| `models.py`           | Shapes for parsed vs normalised rows       |
| `transformer.py`      | Parser output → normalised row             |
| `rules_classifier.py` | Deterministic sorting rules                |
| `llm_classifier.py`   | Smart-label plumbing + caching             |
| `db_writer.py`        | Saves rows + matching logic                |
| `validator.py`        | Benchmark harness                          |
| `holding_pipeline.py` | Investment / holdings paths                |


---

## Prompt templates

YAML lives in `[prompts/](../prompts/)` — safe to commit, versioned like code. Overview: `[prompts/README.md](../prompts/README.md)`.