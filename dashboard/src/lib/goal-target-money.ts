/**
 * Helpers for goal target copy: the engine treats POINT_IN_TIME targets as
 * **today's rupees** (real / purchasing-power today), then compounds by inflation
 * to a nominal amount at the goal date — same idea as `allocate_surplus` in
 * api/services/simulation.py (years = months/12, adj = raw * (1+infl)^years).
 */

/** Default headline CPI when goal-specific inflation is unset (matches simulation defaults). */
export const DEFAULT_HEADLINE_INFLATION_PCT = 6

/**
 * Engine rule (`api/services/simulation.py`): any positive monthly flow below this is
 * treated as zero; use the same threshold when validating recurring goal amounts in the UI.
 */
export const MIN_MONTHLY_GOAL_CONTRIBUTION_INR = 5000

/** Rough monthly equivalent of a recurring payment (for validation vs {@link MIN_MONTHLY_GOAL_CONTRIBUTION_INR}). */
export function recurrenceAmountToMonthlyInr(
  amount: number,
  frequency: string,
): number {
  const f = frequency.trim().toUpperCase()
  if (f === "MONTHLY") return amount
  if (f === "QUARTERLY") return amount / 3
  if (f === "ANNUAL" || f === "YEARLY") return amount / 12
  return amount
}

/** Months — above this we emphasize the nominal future amount in copy (aligns with decomposer horizon). */
export const LONG_HORIZON_MONTHS = 24

/** Parse YYYY-MM-DD as a local calendar date (avoids UTC off-by-one). */
export function parseISODateLocal(iso: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim())
  if (!m) return null
  const y = Number(m[1])
  const mo = Number(m[2])
  const d = Number(m[3])
  if (!y || !mo || !d) return null
  const dt = new Date(y, mo - 1, d)
  if (
    dt.getFullYear() !== y ||
    dt.getMonth() !== mo - 1 ||
    dt.getDate() !== d
  ) {
    return null
  }
  return dt
}

/** Calendar start of "today" in local time (compare to goal dates from `<input type="date">`). */
export function todayLocalDateOnly(): Date {
  const n = new Date()
  return new Date(n.getFullYear(), n.getMonth(), n.getDate())
}

/**
 * Whole months from start to end — mirrors api/services/goal_decomposer.months_between
 * (end must be after start for a positive horizon).
 */
export function monthsBetweenCalendar(start: Date, end: Date): number {
  if (end <= start) return 0
  const y1 = start.getFullYear()
  const m1 = start.getMonth() + 1
  const d1 = start.getDate()
  const y2 = end.getFullYear()
  const m2 = end.getMonth() + 1
  const d2 = end.getDate()
  let total = (y2 - y1) * 12 + (m2 - m1)
  if (d2 < d1) total -= 1
  return Math.max(total, 0)
}

/** API / server inflation resolution — mirrors resolve_goal_inflation (snake_case in JSON). */
export type InflationResolutionLike = {
  annual_pct: number
  category?: string | null
  method?: string
  label?: string
  detail?: string
}

/** Manual goal % wins; else server resolution; else headline (CPI EMA in production). */
export function pickEffectiveInflationPct(opts: {
  goalSpecific: number | null | undefined
  headlinePct: number
  inflationResolution?: InflationResolutionLike | null
}): number {
  const g = opts.goalSpecific
  if (g != null && !Number.isNaN(g)) return g
  const r = opts.inflationResolution
  if (r != null && typeof r.annual_pct === "number" && !Number.isNaN(r.annual_pct)) {
    return r.annual_pct
  }
  return opts.headlinePct
}

/** Short explainer for the inflation row under the target field. */
export function inflationPlanningNote(
  goalSpecific: number | null | undefined,
  resolution: InflationResolutionLike | null | undefined,
): string | null {
  if (goalSpecific != null && !Number.isNaN(goalSpecific)) {
    return "Using the inflation % you entered for this goal."
  }
  if (!resolution?.method) return null
  if (resolution.method === "loan_zero") {
    return "Loan-style cash flows do not have price-level inflation."
  }
  if (resolution.method === "cpi_general_ema") {
    return `Using India headline CPI — about ${resolution.annual_pct}% per year.`
  }
  if (resolution.method === "category_default") {
    const lab = resolution.label ?? resolution.category ?? "category"
    return `Using nominal ${lab} inflation - ${resolution.annual_pct}% per year.`
  }
  if (resolution.method === "user_override") {
    return "Using the inflation % saved for this goal."
  }
  return null
}

/**
 * Nominal rupees needed at the end of *months* full months, if *rawTarget* is today's rupees
 * and prices grow at *annualInflationPct* per year.
 */
export function nominalTargetFromTodaysRupees(
  rawTarget: number,
  months: number,
  annualInflationPct: number,
): number {
  if (rawTarget <= 0 || months <= 0) return rawTarget
  const years = months / 12
  return rawTarget * (1 + annualInflationPct / 100) ** years
}

export type GoalTargetMoneyExplain = {
  /** Show the static "today's rupees" explainer (POINT_IN_TIME lump-sum targets). */
  showTodaysMoneyLine: boolean
  /** Nominal future amount differs meaningfully from raw (inflation > 0, horizon > 0). */
  showNominalFutureLine: boolean
  /** Stronger wording when horizon &gt; 24 months. */
  emphasizeLongHorizon: boolean
  months: number
  effectiveInflationPct: number
  nominalAtDate: number
  formattedDeadline: string
  /** Why this inflation % — category vs headline CPI. */
  planningNote: string | null
}

const EPS = 0.5 // ₹ — ignore float noise

export function explainGoalTargetMoney(opts: {
  rawTarget: number
  targetDateISO: string | null | undefined
  goalSpecificInflation: number | null | undefined
  headlineInflationPct: number
  inflationResolution?: InflationResolutionLike | null
}): GoalTargetMoneyExplain | null {
  const {
    rawTarget,
    targetDateISO,
    goalSpecificInflation,
    headlineInflationPct,
    inflationResolution,
  } = opts
  if (rawTarget <= 0 || !targetDateISO?.trim()) {
    return null
  }
  const end = parseISODateLocal(targetDateISO)
  if (!end) return null
  const start = todayLocalDateOnly()
  const months = monthsBetweenCalendar(start, end)
  const eff = pickEffectiveInflationPct({
    goalSpecific: goalSpecificInflation,
    headlinePct: headlineInflationPct,
    inflationResolution,
  })
  const nominalAtDate = nominalTargetFromTodaysRupees(rawTarget, months, eff)
  const showNominalFutureLine =
    months > 0 && eff > 0 && nominalAtDate > rawTarget + EPS
  const emphasizeLongHorizon = showNominalFutureLine && months > LONG_HORIZON_MONTHS

  const formattedDeadline = end.toLocaleDateString("en-IN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  })

  const planningNote = inflationPlanningNote(
    goalSpecificInflation,
    inflationResolution ?? null,
  )

  return {
    showTodaysMoneyLine: true,
    showNominalFutureLine,
    emphasizeLongHorizon,
    months,
    effectiveInflationPct: eff,
    nominalAtDate,
    formattedDeadline,
    planningNote,
  }
}
