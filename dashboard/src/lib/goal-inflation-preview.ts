/**
 * Mirrors api/services/inflation_service.GOAL_INFLATION_MAP + INFLATION_DEFAULTS
 * for client-side previews when the API has not yet returned inflation_resolution
 * (e.g. new goal form) or when SimulationGoal.inflation_rate is null (use slider +
 * subtype to estimate).
 */

/** Subtype → InflationRate category key (null = no price adjustment). */
export const GOAL_SUBTYPE_INFLATION_CATEGORY: Record<string, string | null> = {
  HOME_PURCHASE: "REAL_ESTATE",
  VEHICLE: "CPI_GENERAL",
  WEDDING: "CPI_GENERAL",
  CHILD_EDUCATION: "EDUCATION",
  RETIREMENT: "CPI_GENERAL",
  TRAVEL: "TRAVEL_DOMESTIC",
  EMERGENCY_FUND: "CPI_GENERAL",
  LOAN_PAYOFF: null,
  CUSTOM: "CPI_GENERAL",
}

/** Static fallbacks — same numeric keys as api/services/inflation_service.INFLATION_DEFAULTS */
export const CATEGORY_INFLATION_DEFAULTS_PCT: Record<string, number> = {
  CPI_GENERAL: 6,
  REAL_ESTATE: 8,
  EDUCATION: 10,
  HEALTHCARE: 10,
  TRAVEL_INTERNATIONAL: 8,
  TRAVEL_DOMESTIC: 6,
}

const CATEGORY_LABELS: Record<string, string> = {
  CPI_GENERAL: "India headline CPI (all items)",
  REAL_ESTATE: "Housing & property costs",
  EDUCATION: "Education costs",
  HEALTHCARE: "Healthcare costs",
  TRAVEL_INTERNATIONAL: "International travel",
  TRAVEL_DOMESTIC: "Domestic travel",
}

/**
 * Rough annual % for planning copy when the server has not computed resolution:
 * CPI_GENERAL → headlineEma (typically from slider / IMF EMA); else category default.
 */
export function estimatedAnnualInflationForSubtype(
  goalSubtype: string | null | undefined,
  headlineCpiEma: number,
): number {
  const st = (goalSubtype || "CUSTOM").trim().toUpperCase()
  const cat = GOAL_SUBTYPE_INFLATION_CATEGORY[st] ?? "CPI_GENERAL"
  if (cat === null) return 0
  if (cat === "CPI_GENERAL") return headlineCpiEma
  return CATEGORY_INFLATION_DEFAULTS_PCT[cat] ?? headlineCpiEma
}

export function categoryLabelForInflationKey(category: string | null | undefined): string {
  if (!category) return "category"
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ").toLowerCase()
}

/** Short name for each subtype — keep in sync with goal creation / edit pickers. */
const SUBTYPE_DISPLAY_NAME: Record<string, string> = {
  LOAN_PAYOFF: "Loan payoff",
  HOME_PURCHASE: "Home purchase",
  VEHICLE: "Vehicle",
  RETIREMENT: "Retirement",
  CHILD_EDUCATION: "Child education",
  EMERGENCY_FUND: "Emergency fund",
  WEDDING: "Wedding",
  TRAVEL: "Travel",
  CUSTOM: "General / custom",
}

/**
 * One line for the inflation-category dropdown: friendly name + default yearly %
 * + what that % is trying to mirror, so people can tell options apart.
 */
export function inflationSelectLabelForSubtype(
  goalSubtype: string | null | undefined,
  headlineCpiEma: number,
): string {
  const st = (goalSubtype || "CUSTOM").trim().toUpperCase()
  const short = SUBTYPE_DISPLAY_NAME[st] ?? st.replace(/_/g, " ").toLowerCase()
  const p = previewInflationResolutionForForm(goalSubtype, headlineCpiEma)
  if (!p) return short
  if (p.method === "loan_zero") {
    return `${short} — 0% a year (we keep the payoff number flat—no extra "price growth" on top)`
  }
  const pct = p.annual_pct.toFixed(1)
  return `${short} — ~${pct}% a year (${p.label})`
}

/** Shape matches `InflationResolutionLike` in goal-target-money — for add-goal preview only. */
export function previewInflationResolutionForForm(
  goalSubtype: string | null | undefined,
  headlineCpiEma: number,
): {
  annual_pct: number
  category: string | null
  method: string
  label: string
} | null {
  const st = (goalSubtype || "CUSTOM").trim().toUpperCase()
  const cat = GOAL_SUBTYPE_INFLATION_CATEGORY[st] ?? "CPI_GENERAL"
  if (cat === null) {
    return {
      annual_pct: 0,
      category: null,
      method: "loan_zero",
      label: "loan payoff",
    }
  }
  const pct = estimatedAnnualInflationForSubtype(goalSubtype, headlineCpiEma)
  if (cat === "CPI_GENERAL") {
    return {
      annual_pct: pct,
      category: "CPI_GENERAL",
      method: "cpi_general_ema",
      label: CATEGORY_LABELS["CPI_GENERAL"],
    }
  }
  return {
    annual_pct: pct,
    category: cat,
    method: "category_default",
    label: categoryLabelForInflationKey(cat),
  }
}

/** Merge server-hydrated metadata with live card edits for hints. */
export function simulationInflationResolutionFromGoal(
  goal: {
    goal_subtype?: string | null
    inflation_rate?: number | null
    inflation_method?: string | null
    inflation_category?: string | null
    inflation_label?: string | null
  },
  generalInflationRate: number,
): {
  annual_pct: number
  category?: string | null
  method: string
  label?: string
} {
  if (goal.inflation_rate != null && goal.inflation_rate !== undefined) {
    return {
      annual_pct: goal.inflation_rate,
      method: goal.inflation_method ?? "user_override",
      category: goal.inflation_category ?? undefined,
      label: goal.inflation_label ?? undefined,
    }
  }
  const p = previewInflationResolutionForForm(goal.goal_subtype, generalInflationRate)
  if (!p) {
    return {
      annual_pct: generalInflationRate,
      method: "cpi_general_ema",
      category: "CPI_GENERAL",
      label: CATEGORY_LABELS["CPI_GENERAL"],
    }
  }
  return p
}
