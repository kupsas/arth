/**
 * Which discovered email sources need which PDF password *ingredients*.
 * Extend this as we add banks — the Config step uses it to show only relevant fields.
 */

import type { OnboardingBackfillSourceRow } from "@/lib/types";

/** True when the user has HDFC **savings** (combined statement / InstaAlerts), not CC-only. */
export function hasHdfcBankSavingsSource(sources: OnboardingBackfillSourceRow[] | undefined): boolean {
  return (sources ?? []).some((s) => s.source_key === "hdfc_savings");
}

/**
 * Name (from profile) + DOB style PDFs — ICICI savings, HDFC savings, HDFC CC, ICICI Direct.
 * CC-only still needs DOB+name; it does not need HDFC **customer ID** (see
 * :func:`hasHdfcBankSavingsSource`).
 */
export function needsProfileNameDobPdfStyle(sources: OnboardingBackfillSourceRow[] | undefined): boolean {
  for (const s of sources ?? []) {
    const k = s.source_key;
    if (k === "icici_savings" || k === "hdfc_savings") return true;
    if (k.startsWith("hdfc_cc")) return true;
    if (k.startsWith("icici_direct")) return true;
  }
  return false;
}

/** NSE / broker PDFs that use PAN. */
export function needsPanPdfSource(sources: OnboardingBackfillSourceRow[] | undefined): boolean {
  for (const s of sources ?? []) {
    const k = s.source_key;
    if (k.includes("icici_direct")) return true;
  }
  return false;
}
