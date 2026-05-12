/**
 * Centralised input guards for onboarding and simulation UIs.
 *
 * - Strips C0 control characters (and line breaks for single-line fields) so pasted
 *   binary / terminal noise cannot break React or balloon payloads.
 * - Enforces max lengths so localStorage + API bodies stay bounded.
 * - Provides strict-ish decimal parsing for goal amounts/years (onboarding + `/simulate`) so `e`,
 *   `Infinity`, and odd Unicode digits do not slip through `type="number"` quirks.
 */

import type { SimulationGoal, SimulationParams } from "@/lib/types"

export const ONBOARDING_INPUT_LIMITS = {
  /** First / last name fields (single line). */
  preclassFirstLastChars: 200,
  /** Aliases, family/friends, account fragments, UPI lists. */
  preclassTextareaChars: 16_000,
  /** Inline counterparty edit in classification review. */
  counterpartyLabelChars: 500,
  /** Pasted API keys (single logical line; newlines stripped). */
  llmApiKeyChars: 8192,
} as const

/** C0 + DEL; for single-line we also drop CR/LF/TAB so one field cannot hold multi-line dumps. */
const RE_CTRL_SINGLE_LINE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\n\r\t]/g
const RE_CTRL_MULTILINE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g

export function stripControlCharacters(
  input: string,
  mode: "single-line" | "multi-line",
): string {
  return input.replace(mode === "single-line" ? RE_CTRL_SINGLE_LINE : RE_CTRL_MULTILINE, "")
}

export function clampTextLength(input: string, max: number): { text: string; truncated: boolean } {
  if (input.length <= max) return { text: input, truncated: false }
  return { text: input.slice(0, max), truncated: true }
}

/** Names and short single-line fields. */
export function guardSingleLineText(input: string, max: number): string {
  const stripped = stripControlCharacters(input, "single-line")
  return clampTextLength(stripped, max).text
}

/** Textareas: keeps newlines; normalises CR/LF. */
export function guardMultilineText(input: string, max: number): string {
  const stripped = stripControlCharacters(input, "multi-line")
  const normalised = stripped.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
  return clampTextLength(normalised, max).text
}

/**
 * API key paste: one logical line, no control chars, bounded length.
 * (Providers issue ASCII-ish secrets; we still strip NULs and newlines.)
 */
export function guardApiKeyInput(input: string, max: number): string {
  const oneLine = input.replace(/[\u0000-\u001F\u007F]/g, "").replace(/\s+/g, "")
  return clampTextLength(oneLine, max).text
}

/** Which LLM provider row in the classifier API key card (matches backend body fields). */
export type ClassifierProviderField = "openai" | "anthropic" | "google"

/** Typical Google Cloud / AI Studio API key length (heuristic). */
const GOOGLE_CLASSIFIER_KEY_LEN = 39

/**
 * Returns a short user-facing message if the pasted string doesn’t look like that provider’s key,
 * or `null` if it passes loose shape checks.
 */
export function describeClassifierKeyShapeError(
  field: ClassifierProviderField,
  guarded: string,
): string | null {
  if (!guarded) return null
  if (field === "google") {
    if (!guarded.startsWith("AIza")) {
      return "That doesn’t look like a Google AI key — copy the one that starts with AIza."
    }
    if (guarded.length !== GOOGLE_CLASSIFIER_KEY_LEN) {
      return `Google AI keys are usually ${GOOGLE_CLASSIFIER_KEY_LEN} characters. Double-check the full key.`
    }
    if (!/^AIza[A-Za-z0-9_-]+$/.test(guarded)) {
      return "That doesn’t look like a complete Google AI key — letters, numbers, hyphens, or underscores only."
    }
    return null
  }
  if (field === "openai") {
    if (!guarded.startsWith("sk-")) {
      return "That doesn’t look like an OpenAI key — copy the one that starts with sk-."
    }
    // Length varies a lot across older vs newer project keys; keep a wide band.
    if (guarded.length < 20 || guarded.length > ONBOARDING_INPUT_LIMITS.llmApiKeyChars) {
      return "That OpenAI key doesn’t look the right length — paste the full key from OpenAI."
    }
    if (!/^sk-[a-zA-Z0-9_-]+$/.test(guarded)) {
      return "That doesn’t look like a complete OpenAI key — use only the characters from your key."
    }
    return null
  }
  // anthropic
  if (!guarded.startsWith("sk-ant-")) {
    return "That doesn’t look like an Anthropic key — copy the one that starts with sk-ant-."
  }
  if (guarded.length < 28 || guarded.length > ONBOARDING_INPUT_LIMITS.llmApiKeyChars) {
    return "That Anthropic key doesn’t look the right length — paste the full key from Anthropic."
  }
  if (!/^sk-ant-[a-zA-Z0-9_-]+$/.test(guarded)) {
    return "That doesn’t look like a complete Anthropic key — use only the characters from your key."
  }
  return null
}

/**
 * Parses user decimal input for goal amount / years.
 * Returns `null` if the string is not a finite real number we should accept while typing
 * (rejects `Infinity`, empty-only, lone `-`, scientific `e` forms when they do not parse as plain decimals).
 */
export function parseGoalDecimalString(raw: string): number | null {
  const t = raw.trim().replace(/,/g, "")
  if (t === "" || t === "-" || t === "+" || t === "." || t === "-." || t === "+.") {
    return null
  }
  // Reject scientific notation strings explicitly (Number("1e309") is Infinity).
  if (/[eE]/.test(t)) return null
  const n = Number(t)
  if (!Number.isFinite(n)) return null
  return n
}

/** Coerce unknown localStorage JSON into a finite number or fallback. */
export function coerceFiniteNumber(value: unknown, fallback: number): number {
  const n = typeof value === "number" ? value : Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** Normalised shape for the pre-classification onboarding step (matches `PreclassDraft`). */
export type PreclassificationDraftNormalized = {
  firstName: string
  lastName: string
  extrasRaw: string
  familyNamesRaw: string
  friendNamesRaw: string
  accountFragmentsRaw: string
  upiIdsRaw: string
}

/**
 * Repairs drafts merged from localStorage: wrong types, huge blobs, and control characters.
 * Safe to call on every mount — returns a structurally equal object when nothing changed.
 */
export function normalizePreclassificationDraft(d: unknown): PreclassificationDraftNormalized {
  const o =
    d !== null && typeof d === "object" && !Array.isArray(d)
      ? (d as Record<string, unknown>)
      : {}
  const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v))
  return {
    firstName: guardSingleLineText(str(o.firstName), ONBOARDING_INPUT_LIMITS.preclassFirstLastChars),
    lastName: guardSingleLineText(str(o.lastName), ONBOARDING_INPUT_LIMITS.preclassFirstLastChars),
    extrasRaw: guardMultilineText(str(o.extrasRaw), ONBOARDING_INPUT_LIMITS.preclassTextareaChars),
    familyNamesRaw: guardMultilineText(
      str(o.familyNamesRaw),
      ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
    ),
    friendNamesRaw: guardMultilineText(
      str(o.friendNamesRaw),
      ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
    ),
    accountFragmentsRaw: guardMultilineText(
      str(o.accountFragmentsRaw),
      ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
    ),
    upiIdsRaw: guardMultilineText(str(o.upiIdsRaw), ONBOARDING_INPUT_LIMITS.preclassTextareaChars),
  }
}

/**
 * When a raw paste becomes empty after sanitising, the user should see a clear message
 * instead of a silent no-op save.
 */
export function describeApiKeySanitiseFailure(raw: string, cleaned: string): string | null {
  if (raw.trim().length > 0 && cleaned.length === 0) {
    return "That paste had no usable key characters after removing spaces and control symbols. Paste the key only, on one line."
  }
  return null
}

// ─────────────────────────────────────────────────────────────────────────────
// Simulation sandbox (`/simulate`) — same decimal + text guards as onboarding.
// ─────────────────────────────────────────────────────────────────────────────

/** Shown under numeric fields when `parseGoalDecimalString` rejects the keystroke / paste. */
export const SIMULATION_INVALID_DECIMAL_MESSAGE =
  "Use digits only (optional decimal point). Letters and scientific notation (e) are not accepted."

export const SIMULATION_INPUT_LIMITS = {
  goalNameChars: 200,
  clientRowIdChars: 80,
  moneyMax: 10_000_000_000_000,
  moneyMin: 0,
  percentMin: 0,
  percentMax: 100,
} as const

/** Aligns with `slider-panel.tsx` monthly surplus cap. */
export const SIMULATION_MONTHLY_SURPLUS_MAX_INR = 1_000_000

const SIM_SALARY_GROWTH_MIN = 0
const SIM_SALARY_GROWTH_MAX = 50
const SIM_GENERAL_INFLATION_MIN = 0
const SIM_GENERAL_INFLATION_MAX = 15

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** True when `YYYY-MM-DD` is a real calendar day (rejects 2020-13-40, etc.). */
export function isValidCalendarIsoDate(iso: string): boolean {
  const t = iso.trim()
  if (!ISO_DATE.test(t)) return false
  const [ys, ms, ds] = t.split("-").map((x) => parseInt(x, 10))
  const d = new Date(ys, ms - 1, ds)
  return (
    !Number.isNaN(d.getTime()) &&
    d.getFullYear() === ys &&
    d.getMonth() === ms - 1 &&
    d.getDate() === ds
  )
}

function clampNum(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n))
}

export function clampSimulationMoneyValue(n: number): number {
  if (!Number.isFinite(n)) return SIMULATION_INPUT_LIMITS.moneyMin
  return clampNum(n, SIMULATION_INPUT_LIMITS.moneyMin, SIMULATION_INPUT_LIMITS.moneyMax)
}

export function clampSimulationPercentValue(n: number): number {
  if (!Number.isFinite(n)) return SIMULATION_INPUT_LIMITS.percentMin
  return clampNum(n, SIMULATION_INPUT_LIMITS.percentMin, SIMULATION_INPUT_LIMITS.percentMax)
}

export function guardSimulationGoalName(input: string): string {
  return guardSingleLineText(input, SIMULATION_INPUT_LIMITS.goalNameChars)
}

/** Accepts only `YYYY-MM-DD` after stripping noise (defensive for odd paste into `type="date"`). */
export function guardIsoDateInput(raw: string): string | null {
  const t = stripControlCharacters(String(raw).trim(), "single-line").slice(0, 32)
  if (!t) return null
  return ISO_DATE.test(t) ? t : null
}

/**
 * Normalises values from `<input type="date">` (and stray pastes) to a strict ISO date.
 * - Truncates an overlong **year** in hyphen form (`202020-07-29` → `2020-07-29`).
 * - Accepts `dd/mm/yyyy` pastes and clamps the year to **four digits** (`29/07/202020` → `2020-07-29`).
 * Returns `undefined` for empty input or strings that cannot be made valid.
 */
export function sanitizeHtmlDateInputValue(raw: string): string | undefined {
  const t = stripControlCharacters(String(raw).trim(), "single-line").slice(0, 32)
  if (!t) return undefined

  const accept = (candidate: string): string | undefined =>
    ISO_DATE.test(candidate) && isValidCalendarIsoDate(candidate) ? candidate : undefined

  const direct = accept(t)
  if (direct) return direct

  const longYear = t.match(/^([0-9]{4,})-([0-9]{1,2})-([0-9]{1,2})$/)
  if (longYear) {
    const y = longYear[1].slice(0, 4)
    const mo = longYear[2].padStart(2, "0")
    const da = longYear[3].padStart(2, "0")
    const v = accept(`${y}-${mo}-${da}`)
    if (v) return v
  }

  const slash = t.match(/^([0-9]{1,2})\/([0-9]{1,2})\/([0-9]+)$/)
  if (slash) {
    let yStr = slash[3]
    if (yStr.length > 4) yStr = yStr.slice(0, 4)
    if (yStr.length !== 4) return undefined
    const mo = slash[2].padStart(2, "0")
    const da = slash[1].padStart(2, "0")
    const v = accept(`${yStr}-${mo}-${da}`)
    if (v) return v
  }

  return undefined
}

function coerceOptionalMoneyField(v: unknown): number | null {
  if (v === null || v === undefined) return null
  if (typeof v === "string" && v.trim() === "") return null
  const n = typeof v === "number" ? v : Number(v)
  if (!Number.isFinite(n)) return null
  return clampSimulationMoneyValue(n)
}

export function sanitizeSimulationGoal(g: SimulationGoal): SimulationGoal {
  const nameRaw = typeof g.name === "string" ? g.name : String(g.name ?? "")
  const cid =
    g.client_row_id == null
      ? undefined
      : guardSingleLineText(String(g.client_row_id), SIMULATION_INPUT_LIMITS.clientRowIdChars)

  const inflationRaw = g.inflation_rate
  let inflation_rate: number | null | undefined = g.inflation_rate
  if (inflationRaw === null || inflationRaw === undefined) {
    inflation_rate = inflationRaw as null | undefined
  } else {
    const inf = typeof inflationRaw === "number" ? inflationRaw : Number(inflationRaw)
    inflation_rate = Number.isFinite(inf) ? clampSimulationPercentValue(inf) : null
  }

  return {
    ...g,
    name: guardSimulationGoalName(nameRaw),
    ...(cid !== undefined ? { client_row_id: cid || undefined } : {}),
    target_amount: coerceOptionalMoneyField(g.target_amount),
    starting_balance: clampSimulationMoneyValue(coerceFiniteNumber(g.starting_balance, 0)),
    allocation_priority: Math.round(
      clampNum(coerceFiniteNumber(g.allocation_priority, 99), 1, 999),
    ),
    expected_return_rate: clampSimulationPercentValue(
      coerceFiniteNumber(g.expected_return_rate, 10),
    ),
    inflation_rate,
    recurrence_amount: coerceOptionalMoneyField(g.recurrence_amount),
    recurrence_start:
      g.recurrence_start == null || g.recurrence_start === ""
        ? g.recurrence_start
        : guardIsoDateInput(String(g.recurrence_start)) ?? null,
    recurrence_end:
      g.recurrence_end == null || g.recurrence_end === ""
        ? g.recurrence_end
        : guardIsoDateInput(String(g.recurrence_end)) ?? null,
    target_date:
      g.target_date == null || g.target_date === ""
        ? g.target_date
        : guardIsoDateInput(String(g.target_date)) ?? null,
  }
}

/**
 * Repairs `sessionStorage` drafts and any corrupted API merge: bounded money, sane %, clean names.
 * Call after `JSON.parse` before feeding `SimulationParams` into React / `POST /api/simulate`.
 */
export function sanitizeSimulationParams(p: SimulationParams): SimulationParams {
  const surplus = clampNum(
    coerceFiniteNumber(p.monthly_surplus, 0),
    0,
    SIMULATION_MONTHLY_SURPLUS_MAX_INR,
  )
  const salary = clampNum(
    coerceFiniteNumber(p.salary_growth_rate ?? 5, 5),
    SIM_SALARY_GROWTH_MIN,
    SIM_SALARY_GROWTH_MAX,
  )
  const genInfl = clampNum(
    coerceFiniteNumber(p.general_inflation_rate ?? 6, 6),
    SIM_GENERAL_INFLATION_MIN,
    SIM_GENERAL_INFLATION_MAX,
  )
  return {
    ...p,
    monthly_surplus: surplus,
    salary_growth_rate: salary,
    general_inflation_rate: genInfl,
    goals: Array.isArray(p.goals) ? p.goals.map(sanitizeSimulationGoal) : [],
  }
}
