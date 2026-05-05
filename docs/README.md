# docs/

Reference documentation, product specs, evaluations, and design notes for the Arth project.

> **Personal financial data:** `docs/personal-data/` is **your own machine-local folder** (gitignored) for statements and benchmarks — it is not shipped with the repo. Each contributor creates it locally if needed.

---

## Directory Structure

```
docs/
  product/            Phase 5 guideline, UI notes (living product docs)
  system-design/      Active architecture notes (e.g. ingestion policy)
  archive/            Superseded scratch notes and old one-off context
  data-notes/         Notes about raw bank data formats and how they map to the schema
  evaluations/        LLM benchmark results, methodology, and model comparisons
  reference/          Reference PDFs (financial OS layer map, Day-1 questions framework)
  personal-data/      ⛔ GITIGNORED — real bank statements, GSheet export, ground-truth CSV
  private/            ⛔ GITIGNORED — personal strategy documents (goals, targets)
```

---

## product/

| File | Description |
|---|---|
| `arth_phase5_guideline_v3_final.md` | Phase 5 guideline — scope and principles; see file header for how it relates to the current codebase (some sections are historical) |
| `Health-Page-UI-thoughts.md` | Design notes for health-related dashboard surfaces |
| `Holdings-Page-UI-thoughts.md` | Design notes for portfolio / holdings UI |
| `Statements-Page-UI-thoughts.pdf` | Design notes for statements UX |

---

## system-design/

| File | Description |
|---|---|
| `INGESTION_PATHS.md` | Declared ingestion hierarchy (email vs file fallbacks) |

Early scratch notes (`understanding_so_far.md`, `notes_prior_chatgpt_chat.md`) live under [`archive/system-design/`](archive/system-design/).

---

## data-notes/

| File | Description |
|---|---|
| `raw_vs_gsheet_notes.md` | Column-by-column mapping of the raw HDFC statement format to the canonical `CanonicalTransaction` schema |
| `GSheet_prompts_used.md` | The original Gemini `=AI()` prompts from Google Sheets — the seed that became the current YAML prompts in `prompts/` |

---

## evaluations/

| Directory / File | Description |
|---|---|
| `llm-benchmark-2026-03/` | Full LLM benchmark (Mar 2026) — 9 models, single-pass vs two-pass, accuracy vs cost. Includes `README.md`, `benchmark.py`, `benchmark_20.json` (test fixture), and `benchmark_results.json` (raw results) |
| `mismatches_pipeline_vs_benchmark.csv` | Pipeline vs ground-truth mismatch log — useful for diagnosing classification failures |

The benchmark README has the decision rationale, final model selection, full results table, and post-benchmark production accuracy numbers.

---

## reference/

| File | Description |
|---|---|
| `Arth _ Layers.pdf` | The 6-layer financial OS framework: Layer 0 (Governance) through Layer 5 (Tax & Legal). The MECE backbone every financial data point maps to. |
| `Arth - Questions to Layers.pdf` | Mapping the 17 Day-1 PRD questions to specific layers — shows which questions require which data. |

---

## personal-data/ (gitignored)

Contains real bank statements and the manually-corrected ground-truth benchmark CSV. Never committed. Placed here manually on each machine (or via symlink).

Key files used by the pipeline:

| Path | Used by |
|---|---|
| `GSheet_Transactions.csv` | `pipeline/validator.py` — the ground-truth benchmark (~648 rows) |
| `HDFC_Savings/` | `pipeline/parsers/hdfc_savings.py` — yearly `.txt` statement files |
| `1905_CC/` | `pipeline/parsers/hdfc_cc.py` — monthly credit card CSVs |
| `5778_CC/` | `pipeline/parsers/hdfc_cc.py` — monthly credit card CSVs |
| `ICICI_Savings/` | `pipeline/parsers/icici_savings.py` — yearly `.pdf` statement files |

---

## private/ (gitignored)

Personal strategy documents — financial goals, targets, and planning notes. These are intentionally excluded from the repo because they contain real financial targets and personal strategy.

Key file: `private/goals_framework.md` — the Goals framework design: how financial goals act as the axis that gives meaning to every layer of the financial OS, preliminary data model, and the Phase 5 agentic vision.
