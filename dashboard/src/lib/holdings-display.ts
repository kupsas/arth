/**
 * holdings-display.ts — shared helpers for the rebuilt /portfolio (holdings) page.
 *
 * The API already sends overall_gain / weight_pct on each Holding, but we still
 * need client-side cost basis for table columns (value at cost) when we want
 * to mirror spreadsheet math without re-fetching.
 */

import type { Holding } from "@/lib/types";

/** Deployed capital from the row: qty × avg cost, else principal for FD-style rows. */
export function holdingCostBasis(h: Holding): number | null {
  const q = h.quantity;
  const avg = h.average_cost_per_unit;
  if (q != null && avg != null && q >= 0 && avg >= 0) {
    return q * avg;
  }
  if (h.principal_amount != null && h.principal_amount > 0) {
    return h.principal_amount;
  }
  return null;
}

/**
 * Share of one holding within a **sleeve** (e.g. all equity rows in the equity
 * table), as percentage points 0–100. Used next to API `weight_pct`, which is
 * vs the full portfolio across all asset classes.
 */
export function weightPercentWithinSleeve(
  currentValue: number | null | undefined,
  sleeveTotalValue: number,
): number | null {
  if (sleeveTotalValue <= 0) return null;
  const v = currentValue ?? 0;
  return (100 * v) / sleeveTotalValue;
}

/** "MUTUAL_FUND" → "Mutual fund" style labels for tables and charts. */
export function prettyAssetClassLabel(assetClass: string): string {
  return assetClass
    .split(/_/g)
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Scroll targets for summary-table rows → section anchors below.
 * Keys match ``asset_class`` strings from the API.
 */
const ASSET_CLASS_SECTION_IDS: Record<string, string> = {
  EQUITY: "holdings-section-equity",
  MUTUAL_FUND: "holdings-section-mf",
  FD: "holdings-section-fd",
  PPF: "holdings-section-ppf",
  NPS: "holdings-section-nps",
  GOLD: "holdings-section-gold",
  SOVEREIGN_GOLD_BOND: "holdings-section-sgb",
  SAVINGS: "holdings-section-savings",
  REAL_ESTATE: "holdings-section-realestate",
  ESOP: "holdings-section-esop",
  OTHER: "holdings-section-other",
};

export function scrollToHoldingsSection(assetClass: string): void {
  const id = ASSET_CLASS_SECTION_IDS[assetClass] ?? "holdings-section-other";
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Heuristic: corporate bond / NCD style rows stored under OTHER. */
export function isLikelyCorporateBond(h: Holding): boolean {
  if (h.asset_class !== "OTHER") return false;
  const blob = `${h.name} ${h.symbol ?? ""}`.toLowerCase();
  return /\b(bond|ncd|debenture|commercial paper|cp\b)\b/i.test(blob);
}

/** LARGE_CAP → "Large cap" for grouping headers. */
export function prettyMarketCapClass(raw: string | null | undefined): string {
  if (!raw) return "Unclassified";
  return raw
    .split(/_/g)
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Batch-returns payloads use ``annualized_return`` as a **decimal** for XIRR
 * (e.g. 0.12 = 12%). Some fixed-return paths may already be in percent; we
 * normalise to **percentage points** (12, not 0.12) for display and coloring.
 */
export function annualizedReturnPercentPoints(
  annualized: unknown,
): number | null {
  if (annualized == null || typeof annualized !== "number") return null;
  if (Number.isNaN(annualized)) return null;
  return Math.abs(annualized) <= 1 ? annualized * 100 : annualized;
}

export function formatAnnualizedReturnForDisplay(
  annualized: unknown,
): string | null {
  const pct = annualizedReturnPercentPoints(annualized);
  if (pct == null) return null;
  return `${pct.toFixed(1).replace(/\.0$/, "")}%`;
}
