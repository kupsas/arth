# Prompts

LLM prompt templates for transaction classification. All prompts live in YAML files — structured, git-versioned, and safe to commit. No raw prompt strings in Python code.

---

## Why YAML

The prompts have had three homes:

1. **Google Sheets `=AI()` calls (mid-2025)** — prompts were literal strings typed into spreadsheet cells. Worked but impossible to version or test programmatically.
2. **Inline Python strings** — moved into code when the pipeline was built. Worked but created messy diffs and buried prompt changes inside unrelated commits.
3. **YAML files (current)** — the right final home, for these reasons:
   - **Git-versioned:** `git log prompts/classify_single_pass.yaml` shows every prompt change ever. Branches let you A/B test changes without touching `main`.
   - **Structured metadata:** Each file carries version, description, the model it was tested with, and last-updated date — alongside the actual template.
   - **Human-readable:** Anyone can read and edit a YAML file without touching Python.
   - **Safe to commit:** No API keys, no personal data — just template text with `{variable}` placeholders.

---

## Files

| File | Purpose |
|---|---|
| `classify_single_pass.yaml` | **Active prompt.** Sorts all fields in one model call. |
| `enums.yaml` | Canonical enum values (`TxnType`, `Channel`, `UPIType`, `CounterpartyCategory`). Imported by the classifier prompt. |
| `few_shot_examples.yaml` | Labeled example transactions for few-shot guidance. Shared across prompts. |
| `classify_two_pass_fields.yaml` | Archived. Two-pass strategy — Step 1: fields. Kept for reference only. |
| `classify_two_pass_category.yaml` | Archived. Two-pass strategy — Step 2: category. Kept for reference only. |

---

## Active Prompt: `classify_single_pass.yaml`

The single-pass prompt classifies all fields — `txn_type`, `upi_type`, `counterparty`, `counterparty_category` — in one API call per batch.

**Why single-pass won:** The benchmark (March 2026) tested single-pass vs two-pass across 9 models. Single-pass outperformed two-pass on every model. The chain-of-thought instruction in the prompt ("first identify txn_type and counterparty, then use their combination to pick category") was sufficient. Splitting into two API calls just added error surface and doubled the cost.

Full benchmark results and methodology: [`docs/evaluations/llm-benchmark-2026-03/README.md`](../docs/evaluations/llm-benchmark-2026-03/README.md)

---

## YAML File Structure

Each prompt file follows this structure:

```yaml
version: "2.0"
description: "Single-pass classification for all fields"
tested_with: "gemini-3.1-flash-lite"
last_updated: "2026-03-13"

system_template: |
  You are a financial transaction classifier for Indian bank statements.
  ...

user_template: |
  Classify these {n} transactions:
  {transactions}
```

`pipeline/prompts.py` is a thin loader — it reads the YAML and interpolates `{variable}` placeholders at runtime. It does not contain any prompt text itself.

---

## How to Modify Prompts Safely

1. **Edit the YAML file** — change wording, add/remove few-shot examples, tighten or relax rules.
2. **Run the benchmark** to measure the impact:
   ```bash
   python3 docs/evaluations/llm-benchmark-2026-03/benchmark.py
   ```
3. **Compare accuracy numbers** — if they improved (or held steady with better cost), commit the change with a descriptive message like `prompts: add Swiggy sub-brand examples`.
4. **Never rename or delete enum values** without also updating `pipeline/models.py` — the enums in `enums.yaml` must always match the Python enums or the LLM output will fail validation.

**Common changes and their risk level:**

| Change | Risk | Notes |
|---|---|---|
| Add a few-shot example | Low | More examples generally help; watch for overfitting to that specific case |
| Rephrase a rule or instruction | Medium | Run benchmark before and after to confirm direction |
| Add a new category | High | Must add to `CounterpartyCategory` enum in `pipeline/models.py` first, then add to `enums.yaml` |
| Change output format (JSON keys, structure) | High | `llm_classifier.py`'s response parser must be updated to match |

---

## Few-Shot Examples

`few_shot_examples.yaml` contains labeled transactions used for few-shot guidance. These examples were chosen to cover:

- Easy cases (UPI merchant, salary)
- Medium cases (NEFT transfers, broker payments)
- Hard cases (ambiguous UPI names, Uber driver payments, BharatPe merchants)

When adding examples, prioritize the hard cases — the easy ones the LLM already gets right without examples. Covering the hard cases is where few-shot actually moves the accuracy needle.

**The Swiggy sub-brand problem:** ~8% of CC transactions are Swiggy spends whose raw descriptions don't distinguish between Instamart, Dineout, and Food delivery. This is a data quality gap that few-shot examples cannot solve — the signal simply isn't in the narration string. It's tracked as a known limitation.
