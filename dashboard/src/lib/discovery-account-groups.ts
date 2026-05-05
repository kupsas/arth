/**
 * Groups Gmail discovery rows into the three onboarding buckets (banks / demat / cards)
 * and merges rows by **institution** (HDFC, ICICI, …) so the UI can show a short list with
 * accordions for per-sender detail.
 *
 * Institution names are inferred from `display_name` and sender email — they are labels for
 * the UI, not database IDs.
 */

import type { OnboardingDiscoveryStreamRow } from "@/lib/api"

/** Coarse bucket aligned with scraper `source_type` (`savings` | `broker` | `credit_card`). */
export type DiscoveryUiCategory = "bank" | "demat" | "credit"

export type DiscoveryInstitutionGroup = {
  /** Short label, e.g. "HDFC", "ICICI", "NSE" */
  institution: string
  /** Source rows merged into this institution (same category). */
  rows: OnboardingDiscoveryStreamRow[]
  /** Sum of `email_count_estimate` across rows (rough volume hint). */
  totalMessages: number
}

const CATEGORY_ORDER: DiscoveryUiCategory[] = ["bank", "demat", "credit"]

/** Maps normalized `source_type` strings to our three UI sections. */
export function sourceTypeToCategory(sourceType: string): DiscoveryUiCategory {
  const t = sourceType.trim().toLowerCase().replace(/\s+/g, "_")
  if (t === "savings" || t === "saving") return "bank"
  if (t === "broker" || t === "demat") return "demat"
  if (t === "credit_card" || t === "creditcard") return "credit"
  // Unknown pipeline values — keep the row visible under banks rather than hiding it.
  return "bank"
}

/**
 * Match common Indian bank / broker names at the start of the Gmail display name.
 * Order matters: longer phrases before shorter prefixes when needed.
 */
const INSTITUTION_FROM_DISPLAY: Array<{ re: RegExp; label: string }> = [
  { re: /^Yes\s+Bank\b/i, label: "Yes Bank" },
  { re: /^State\s+Bank\b/i, label: "SBI" },
  { re: /^HDFC\b/i, label: "HDFC" },
  { re: /^ICICI\b/i, label: "ICICI" },
  { re: /^Axis\b/i, label: "Axis" },
  { re: /^Kotak\b/i, label: "Kotak" },
  { re: /^SBI\b/i, label: "SBI" },
  { re: /^NSE\b/i, label: "NSE" },
]

function institutionFromDomain(senderEmail: string): string | null {
  const host = senderEmail.split("@")[1]?.toLowerCase() ?? ""
  if (!host) return null
  if (host.includes("hdfc")) return "HDFC"
  if (host.includes("icici")) return "ICICI"
  if (host.includes("axis")) return "Axis"
  if (host.includes("sbi")) return "SBI"
  if (host.includes("kotak")) return "Kotak"
  if (host.includes("yesbank") || host.includes("yes.bank")) return "Yes Bank"
  if (host.includes("nse")) return "NSE"
  return null
}

/**
 * Derive a single institution label for one discovery row (for grouping).
 */
export function inferInstitutionLabel(row: OnboardingDiscoveryStreamRow): string {
  const name = row.display_name?.trim() || ""
  for (const { re, label } of INSTITUTION_FROM_DISPLAY) {
    if (re.test(name)) return label
  }
  const fromDomain = institutionFromDomain(row.sender_email)
  if (fromDomain) return fromDomain

  // Fallback: first "word" from display name (handles odd marketing labels).
  const first = name.split(/\s+/)[0]?.replace(/[^a-zA-Z0-9]/g, "") ?? ""
  if (first.length >= 2) {
    return first.charAt(0).toUpperCase() + first.slice(1).toLowerCase()
  }
  return row.sender_email || "Unknown"
}

function buildInstitutionGroup(
  institution: string,
  rows: OnboardingDiscoveryStreamRow[],
): DiscoveryInstitutionGroup {
  const sorted = [...rows].sort((a, b) => a.sender_email.localeCompare(b.sender_email))
  let total = 0
  for (const r of sorted) {
    total += r.email_count_estimate
  }
  return {
    institution,
    rows: sorted,
    totalMessages: total,
  }
}

export const discoveryCategoryMeta: Record<
  DiscoveryUiCategory,
  { title: string; emptyHint: string }
> = {
  bank: {
    title: "Banks found",
    emptyHint: "No savings or statement senders yet — try discovery again after new mail arrives.",
  },
  demat: {
    title: "Demat accounts found",
    emptyHint: "No broker or trade-confirmation senders yet — some brokers only show up in PDF statements.",
  },
  credit: {
    title: "Credit cards found",
    emptyHint: "No credit card alert senders yet — check that the right Gmail inbox is connected.",
  },
}

/**
 * Partition stream rows into the three categories, then group by inferred institution.
 */
export function groupDiscoveryRowsForUi(rows: OnboardingDiscoveryStreamRow[]): Record<
  DiscoveryUiCategory,
  DiscoveryInstitutionGroup[]
> {
  const buckets: Record<DiscoveryUiCategory, OnboardingDiscoveryStreamRow[]> = {
    bank: [],
    demat: [],
    credit: [],
  }

  for (const row of rows) {
    buckets[sourceTypeToCategory(row.source_type)].push(row)
  }

  const result = {} as Record<DiscoveryUiCategory, DiscoveryInstitutionGroup[]>
  for (const cat of CATEGORY_ORDER) {
    const byInst = new Map<string, OnboardingDiscoveryStreamRow[]>()
    for (const row of buckets[cat]) {
      const label = inferInstitutionLabel(row)
      const list = byInst.get(label) ?? []
      list.push(row)
      byInst.set(label, list)
    }
    let groups = [...byInst.entries()].map(([institution, list]) =>
      buildInstitutionGroup(institution, list),
    )
    groups.sort((a, b) => a.institution.localeCompare(b.institution))
    // Demat: NSE trade emails duplicate other broker mail — hide NSE when any other demat line exists.
    if (cat === "demat") {
      const withoutNse = groups.filter((g) => g.institution !== "NSE")
      if (withoutNse.length < groups.length && withoutNse.length > 0) {
        groups = withoutNse
      }
    }
    result[cat] = groups
  }
  return result
}
