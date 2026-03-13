# LLM Model Benchmark: Quality vs Cost (March 2026)

## Objective

Find the best LLM model for transaction classification (counterparty, counterparty_category, txn_type, upi_type) given our constraint: **quality first, cost second, speed doesn't matter**.

## Setup

- **Test fixture:** 20 hand-picked transactions from the HDFC savings statement, covering easy/medium/hard classification difficulty
- **Ground truth:** Manually classified values from the GSheet (GSheet_Transactions.csv)
- **Prompt strategies tested:** Single-pass (all fields in one call) vs Two-pass (fields first, then category separately using "txn_type + counterparty")
- **Models tested:** 9 models across 3 providers (10th, gemini-3-flash, timed out consistently)

## Results

| Model | Strategy | Accuracy | Cat. Acc | Cost (20 txns) | $/correct |
|---|---|---|---|---|---|
| **gemini-3.1-flash-lite** | **single** | **81.1%** | **85.0%** | $0.0025 | $0.000042 |
| gemini-2.5-flash | single | 79.7% | 75.0% | $0.0044 | $0.000074 |
| claude-opus-4-6 | two-pass | 79.7% | 75.0% | $0.0542 | $0.000918 |
| claude-opus-4-6 | single | 78.4% | 75.0% | $0.0418 | $0.000720 |
| gemini-3.1-flash-lite | two-pass | 78.4% | 70.0% | $0.0033 | $0.000057 |
| gemini-2.5-flash-lite | single | 77.0% | 75.0% | $0.0008 | $0.000014 |
| gpt-5-mini | single | 75.7% | 70.0% | $0.0082 | $0.000146 |
| claude-haiku-4-5 | single | 75.7% | 75.0% | $0.0103 | $0.000184 |
| gpt-5-nano | single | 73.0% | 60.0% | $0.0046 | $0.000085 |
| claude-sonnet-4-6 | single | 71.6% | 70.0% | $0.0251 | $0.000473 |
| gemini-3-flash | both | ERROR (timeout) | - | - | - |

## Key Findings

1. **Single-pass wins over two-pass for every model.** The chain-of-thought instruction in the single-pass prompt ("first identify txn_type and counterparty, then use their combination to pick category") was sufficient. Splitting into two separate API calls just introduced more error surface.

2. **gemini-3.1-flash-lite is the best value.** 81% accuracy at $0.0025 per 20 transactions. For our ~650 transaction dataset, that would cost roughly $0.08 per full pipeline run.

3. **Gemini dominates the value tier.** Top 3 value positions are all Gemini. They're also the fastest (2-6s per run vs 50-90s for OpenAI GPT-5).

4. **Claude Opus is the quality ceiling but 22x more expensive** than gemini-3.1-flash-lite for only ~2% better accuracy.

5. **OpenAI GPT-5 models are slow and verbose.** 2-3x more tokens for the same task, 50-90s per run. Not competitive.

6. **The hard cases remain hard for everyone.** Uber driver names (P2P payments that should be "Transport & Fuel"), Ed Sheeran concert (inflow from a person for an event), and BharatPe merchants (person name that's actually a small business) tripped up all models.

## Decision

- **Primary model:** gemini-3.1-flash-lite (single-pass)
- **Fallback chain:** gemini-2.5-flash -> claude-haiku-4-5 -> gpt-5-mini
- **Next step (from the time of the benchmark):** Improve prompt engineering to push accuracy from 81% toward 90%+, focusing on the hard cases (Uber drivers, ambiguous merchants, event-related transfers).

## Postscript (later in March 2026)

After applying the benchmark learnings plus substantial rules and ground-truth work on the full ~647-row HDFC savings dataset, the production pipeline now achieves:

- **txn_type:** ~98.7%
- **upi_type:** ~98.1%
- **counterparty:** ~94.9%
- **counterparty_category:** ~93.7%

Key contributors:

- A strong **deterministic rules layer** for UPI P2P vs P2M, UPI Lite, rent, hotels/travel, self-transfers, and known merchants.
- A much tighter **name matching strategy** for people (family, friends, acquaintances, self) and merchants.
- Iterative **ground-truth alignment** where the sheet was updated whenever the clarified intent or rules made the pipeline output unambiguously better than the original labels.

## Model Pricing (as of March 2026, per 1M tokens)

| Model | Input | Output |
|---|---|---|
| claude-haiku-4-5 | $1.00 | $5.00 |
| claude-sonnet-4-6 | $3.00 | $15.00 |
| claude-opus-4-6 | $5.00 | $25.00 |
| gpt-5-mini-2025-08-07 | $0.25 | $2.00 |
| gpt-5-nano-2025-08-07 | $0.05 | $0.40 |
| gemini-3.1-flash-lite-preview | $0.25 | $1.50 |
| gemini-3-flash-preview | $0.50 | $3.00 |
| gemini-2.5-flash | $0.30 | $2.50 |
| gemini-2.5-flash-lite | $0.10 | $0.40 |

## Files in This Archive

- `README.md` — this summary
- `benchmark_20.json` — the 20-transaction test fixture with ground truth
- `benchmark_results.json` — raw results from all 18 benchmark runs (per-field scores, token counts, costs, per-transaction details)
- `test_api_keys.py` — smoke test script that verified all API keys and model names
