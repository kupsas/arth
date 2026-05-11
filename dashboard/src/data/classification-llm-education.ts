/**
 * Onboarding numbers for the optional LLM API key step (`onboarding-optional-llm-keys.tsx`).
 *
 * - **Indicative %** — adjust by hand when product story changes.
 * - **$/100** — from primary model `cost_usd` in `data/test/benchmark_results.json` (single-pass
 *   `gemini-3.1-flash-lite`), scaled: `cost_usd * (100 / 20)` for the 20-txn stress run.
 *   After re-running the benchmark, update `ONBOARDING_PRIMARY_COST_USD_PER_100` (or use
 *   `scripts/print_benchmark_chain_summary.py` and copy the primary line).
 */

export const ONBOARDING_INDICATIVE_OVERALL_PCT = {
  rulesOnly: 92,
  withCloudModel: 96,
} as const;

/** Indicative cloud-classified rows per 1,000 all transactions (drives the cost one-liner). */
export const ONBOARDING_INDICATIVE_CLOUD_ROWS_PER_1000 = 10;

/**
 * USD to run the primary cloud model on 100 “stress-profile” rows (same scale as the internal LLM cost benchmark).
 * Source: `cost_usd` 0.00249375 for 20 txns → ×5.
 */
export const ONBOARDING_PRIMARY_COST_USD_PER_100 = 0.00249375 * (100 / 20);

export function formatUsd(n: number, digits = 4): string {
  // Use en-IN so digit grouping matches the rest of the app (Indian notation).
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
}

/**
 * Cost to classify `cloudRowCount` rows, given the $/100 figure from the benchmark.
 */
export function costUsdForCloudRowCount(
  cloudRowCount: number,
  costUsdPer100CloudClassified: number,
): number {
  return (cloudRowCount / 100) * costUsdPer100CloudClassified;
}
