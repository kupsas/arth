"use client"

/**
 * Template-based **goal** wizard (Track 2 Phase 4c).
 *
 * - Picks a template (house, car, retirement, … or custom).
 * - Templates are grouped by **goal_class**: one-time (``POINT_IN_TIME``) vs
 *   **recurring** (``RECURRING_CASH_FLOW`` — vacation, loan EMI), using
 *   ``template_sections`` from the API (fallback mirrors the same layout).
 * - **One-time:** target amount, horizon in years → ``target_date`` (from today).
 * - **Recurring:** per-period amount, **recurrence start** (date), duration in years →
 *   ``recurrence_start``, ``recurrence_end`` (start + duration), and ``target_date``
 *   (same as end) on ``POST /api/goals`` — matches :class:`api.models.Goal`.
 * - Fetches `GET /api/onboarding/goal-templates` for inflation defaults + a live
 *   future-value **preview** (target in today's rupees → inflation-adjusted stub).
 * - Submits with `useCreateGoal` → `POST /api/goals` using the same schema as
 *   the main Goals table.
 * - Subscribes to ``useGoals()`` so **saved goals appear on /setup** after create
 *   (React Query invalidation refetches here too — not only on ``/goals``).
 * - Each saved row has **Delete** (same API as the main Goals page).
 *
 * The preview math is intentionally simple display logic; the full simulation
 * engine still runs elsewhere when you use surplus / what-if features.
 *
 * **Draft persistence:** Template choice, amount, horizon, and recurring start
 * date are debounced to localStorage. Cleared after a goal is created successfully.
 */

import * as React from "react"
import { Loader2, Sparkles, Trash2 } from "lucide-react"

import Link from "next/link"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  ONBOARDING_GOAL_TEMPLATE_SECTIONS_FALLBACK,
  ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS,
  ONBOARDING_GOAL_TEMPLATES_FALLBACK,
} from "@/data/onboarding-goal-templates-fallback"
import { useFormDraft } from "@/hooks/use-form-draft"
import { useCreateGoal, useDeleteGoal, useGoals } from "@/hooks/use-goals"
import { useOnboardingGoalTemplates } from "@/hooks/use-onboarding-goal-templates"
import { ApiError } from "@/lib/api"
import {
  coerceFiniteNumber,
  parseGoalDecimalString,
} from "@/lib/onboarding-input-validation"
import { cn, formatCurrency } from "@/lib/utils"
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error"
import type { OnboardingGoalTemplate, GoalCreate } from "@/lib/types"

/** Local calendar today as YYYY-MM-DD (avoid UTC-only ``toISOString()`` surprises). */
function todayIsoLocal(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function formatLocalYmd(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

/** True for ``YYYY-MM-DD`` that parses to a real calendar date. */
function isValidIsoDate(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s.trim())) return false
  const [ys, ms, ds] = s.split("-").map((x) => parseInt(x, 10))
  const t = new Date(ys, ms - 1, ds)
  return (
    !Number.isNaN(t.getTime()) &&
    t.getFullYear() === ys &&
    t.getMonth() === ms - 1 &&
    t.getDate() === ds
  )
}

/**
 * Add a **fractional** number of years as calendar months (duration from a start date).
 * Used for recurring ``recurrence_end`` and one-time ``target_date`` from today.
 */
function addYearsMonthsFromIso(startIso: string, years: number): string {
  const yFloat = Number(years)
  if (!Number.isFinite(yFloat) || yFloat < 0) {
    return isValidIsoDate(startIso) ? startIso : todayIsoLocal()
  }
  const cappedYears = Math.min(
    ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS,
    Math.max(0, yFloat),
  )
  const totalMonths = Math.min(
    ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS * 12,
    Math.max(0, Math.round(cappedYears * 12)),
  )
  const parts = startIso.trim().split("-")
  if (parts.length !== 3 || parts.some((p) => p === "")) {
    const t = new Date()
    t.setMonth(t.getMonth() + totalMonths)
    return Number.isNaN(t.getTime()) ? todayIsoLocal() : formatLocalYmd(t)
  }
  const yr = parseInt(parts[0], 10)
  const mo = parseInt(parts[1], 10)
  const da = parseInt(parts[2], 10)
  const t = new Date(yr, mo - 1, da)
  if (Number.isNaN(t.getTime())) {
    const n = new Date()
    n.setMonth(n.getMonth() + totalMonths)
    return formatLocalYmd(n)
  }
  t.setMonth(t.getMonth() + totalMonths)
  if (Number.isNaN(t.getTime())) return todayIsoLocal()
  return formatLocalYmd(t)
}

function buildCreatePayload(
  tpl: OnboardingGoalTemplate,
  draft: Pick<GoalsDraft, "amount" | "years" | "recurrence_start_iso">,
  /** One-time goals only: ``target_date`` from today + horizon. */
  pitTargetDateIso: string,
): GoalCreate {
  // Recurring: amount per period + explicit window → recurrence_start / recurrence_end / target_date.
  if (tpl.goal_class === "RECURRING_CASH_FLOW") {
    const freq = (tpl.recurrence_frequency ?? "ANNUAL") as NonNullable<
      GoalCreate["recurrence_frequency"]
    >
    const subtype = (tpl.goal_subtype ?? "CUSTOM") as NonNullable<GoalCreate["goal_subtype"]>
    const start = draft.recurrence_start_iso.trim()
    const recurrenceEndIso = addYearsMonthsFromIso(start, draft.years)
    return {
      name: tpl.name,
      goal_type: tpl.goal_type as GoalCreate["goal_type"],
      target_amount: draft.amount,
      // Align book-end with the recurring obligation window (simulation + list UIs).
      target_date: recurrenceEndIso,
      priority: tpl.suggested_priority,
      time_horizon: (tpl.time_horizon ?? "ANNUAL") as GoalCreate["time_horizon"],
      funding_mode: (tpl.funding_mode ?? "EVENT") as GoalCreate["funding_mode"],
      goal_class: "RECURRING_CASH_FLOW",
      goal_subtype: subtype,
      recurrence_amount: draft.amount,
      recurrence_frequency: freq,
      recurrence_start: start,
      recurrence_end: recurrenceEndIso,
      expected_return_rate: tpl.default_expected_return_rate,
      goal_specific_inflation_rate: tpl.inflation_annual_percent,
    }
  }

  return {
    name: tpl.name,
    goal_type: tpl.goal_type as GoalCreate["goal_type"],
    target_amount: draft.amount,
    target_date: pitTargetDateIso,
    priority: tpl.suggested_priority,
    time_horizon: (tpl.time_horizon ?? "MULTI_YEAR") as GoalCreate["time_horizon"],
    funding_mode: (tpl.funding_mode ?? "ACCUMULATION") as GoalCreate["funding_mode"],
    goal_class: (tpl.goal_class ?? "POINT_IN_TIME") as GoalCreate["goal_class"],
    goal_subtype: (tpl.goal_subtype ?? "CUSTOM") as GoalCreate["goal_subtype"],
    expected_return_rate: tpl.default_expected_return_rate,
    // Seed for display — the goals API can still re-resolve from subtype.
    goal_specific_inflation_rate: tpl.inflation_annual_percent,
  }
}

/** Persisted between refreshes — no secrets here, only planning numbers. */
type GoalsDraft = {
  selected: string | null
  amount: number
  years: number
  recurrence_start_iso: string
}

const GOALS_STORAGE_KEY = "arth_onboarding_goals"

const GOALS_DEFAULT: GoalsDraft = {
  selected: null,
  amount: 50_00_000,
  years: 5,
  recurrence_start_iso: "",
}

/** Upper bound for “today’s ₹” input — large enough for any realistic goal, small enough to stay numeric-safe. */
const GOAL_WIZARD_AMOUNT_MAX = 10_000_000_000_000

/** Must match `GET /api/onboarding/goal-templates` Query `le` for `years` (422 if exceeded). */
function clampYearsForGoalTemplatesApi(years: number): number {
  const y = Number(years)
  if (!Number.isFinite(y)) return GOALS_DEFAULT.years
  return Math.min(ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS, Math.max(0, y))
}

/** Keeps the persisted draft in a range the API accepts so we do not blank the template list on 422. */
function clampYearsForDraft(years: number): number {
  const y = Number(years)
  if (!Number.isFinite(y) || y < 0.25) return GOALS_DEFAULT.years
  return Math.min(ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS, y)
}

/** While typing, never let years exceed the API cap (avoids invalid `Date` in `addYears`). Lower bound fixed on blur. */
function clampYearsDuringInput(years: number): number {
  const y = Number(years)
  if (!Number.isFinite(y)) return 0
  return Math.min(ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS, Math.max(0, y))
}

function clampAmountDuringInput(amount: number): number {
  const a = Number(amount)
  if (!Number.isFinite(a)) return 0
  return Math.min(GOAL_WIZARD_AMOUNT_MAX, Math.max(0, a))
}

function clampAmountForDraft(amount: number): number {
  const a = Number(amount)
  if (!Number.isFinite(a) || a < 1) return GOALS_DEFAULT.amount
  return Math.min(GOAL_WIZARD_AMOUNT_MAX, a)
}

/** Card subtitle: rupee range + cadence for recurring + CPI line. */
function templateCardFooter(t: OnboardingGoalTemplate): string {
  const lo = t.default_target_amount_min.toLocaleString("en-IN")
  const hi = t.default_target_amount_max.toLocaleString("en-IN")
  const inf = `${t.inflation_annual_percent}%/yr ${t.inflation_rate_label}`
  if (t.goal_class === "RECURRING_CASH_FLOW") {
    const cadence = (t.recurrence_frequency ?? "ANNUAL").toLowerCase().replaceAll("_", " ")
    const hint =
      t.recurrence_amount_hint != null
        ? ` · hint ₹${t.recurrence_amount_hint.toLocaleString("en-IN")}/${cadence}`
        : ""
    return `~₹${lo}–₹${hi} (${cadence})${hint} · ${inf}`
  }
  return `~₹${lo}–₹${hi} · ${inf}`
}

export function GoalTemplateWizard() {
  const { mutate: create, isPending, isError, error } = useCreateGoal()
  const { data: savedGoals, isLoading: savedGoalsLoading } = useGoals()
  const { mutate: deleteGoal } = useDeleteGoal()
  const [deletingGoalId, setDeletingGoalId] = React.useState<number | null>(null)
  const { value: draft, setValue: setDraft, clearDraft } = useFormDraft(
    GOALS_STORAGE_KEY,
    GOALS_DEFAULT,
  )
  const [done, setDone] = React.useState(false)
  const [amountParseErr, setAmountParseErr] = React.useState<string | null>(null)
  const [yearsParseErr, setYearsParseErr] = React.useState<string | null>(null)

  // Repair localStorage: wrong types, non-finite numbers, out-of-range years (422 on templates API).
  React.useEffect(() => {
    setDraft((d) => {
      const selected =
        d.selected === null || typeof d.selected === "string" ? d.selected : GOALS_DEFAULT.selected
      const rawY = coerceFiniteNumber(d.years, GOALS_DEFAULT.years)
      const rawA = coerceFiniteNumber(d.amount, GOALS_DEFAULT.amount)
      const y = clampYearsForDraft(rawY)
      const nextA = clampAmountForDraft(rawA)
      const rsRaw =
        typeof d.recurrence_start_iso === "string" && d.recurrence_start_iso.trim() !== ""
          ? d.recurrence_start_iso.trim()
          : todayIsoLocal()
      const recurrence_start_iso = isValidIsoDate(rsRaw) ? rsRaw : todayIsoLocal()
      if (
        y === d.years &&
        nextA === d.amount &&
        selected === d.selected &&
        recurrence_start_iso === d.recurrence_start_iso
      ) {
        return d
      }
      return { selected, years: y, amount: nextA, recurrence_start_iso }
    })
  }, [setDraft])

  const templateQueryParams = React.useMemo(
    () => ({
      target_amount: draft.amount,
      years: clampYearsForGoalTemplatesApi(draft.years),
      template_id: draft.selected ?? undefined,
    }),
    [draft.amount, draft.years, draft.selected],
  )

  const {
    data,
    isLoading,
    isError: isTemplatesQueryError,
    error: templatesError,
  } = useOnboardingGoalTemplates(templateQueryParams, {
    enabled: true,
    // Validation errors will not succeed on retry — avoid hammering the API / flickering the UI.
    retry: (failureCount, err) => {
      if (err instanceof ApiError && err.status === 422) return false
      return failureCount < 2
    },
  })

  const extraHeadline = data?.headline_preview
  const extraHeadlineRecurring = data?.headline_preview_recurring
  const templateSections =
    data?.template_sections ?? ONBOARDING_GOAL_TEMPLATE_SECTIONS_FALLBACK
  const templates =
    data?.templates && data.templates.length > 0
      ? data.templates
      : isTemplatesQueryError
        ? ONBOARDING_GOAL_TEMPLATES_FALLBACK
        : []
  const active: OnboardingGoalTemplate | undefined = templates.find(
    (t) => t.id === draft.selected,
  )
  const preview =
    active?.preview ??
    (draft.selected == null ? extraHeadline : undefined)
  const isRecurringActive = active?.goal_class === "RECURRING_CASH_FLOW"
  const pitTargetDateIso = addYearsMonthsFromIso(todayIsoLocal(), draft.years)
  const recurringStartOk = isValidIsoDate(draft.recurrence_start_iso)
  const recurringEndIso =
    isRecurringActive && recurringStartOk
      ? addYearsMonthsFromIso(draft.recurrence_start_iso, draft.years)
      : null

  const amountOk =
    Number.isFinite(draft.amount) && draft.amount >= 1 && draft.amount <= GOAL_WIZARD_AMOUNT_MAX
  const yearsOk =
    Number.isFinite(draft.years) &&
    draft.years >= 0.25 &&
    draft.years <= ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS
  const canCreateGoal =
    Boolean(active) &&
    amountOk &&
    yearsOk &&
    !amountParseErr &&
    !yearsParseErr &&
    (!isRecurringActive || recurringStartOk)

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <Sparkles className="size-6" />
          Goals
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          Pick a template below. <strong>One-time goals</strong> are a corpus toward a target
          date; <strong>recurring</strong> rows are monthly or yearly run-rates (EMI, annual
          vacation). Amounts are in <strong>today’s rupees</strong>; previews spell out which
          mechanism applies.
        </p>
      </div>

      {isLoading && !data && (
        <p className="text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 className="size-4 animate-spin" />
          Loading templates and CPI hints…
        </p>
      )}

      {isTemplatesQueryError && (
        <p className="text-sm text-amber-600 dark:text-amber-500" role="alert">
          {templatesError instanceof ApiError
            ? `Live inflation hints unavailable (${templatesError.status}). You can still pick a template below; try refreshing if the issue persists.`
            : "Live inflation hints unavailable. You can still pick a template below; try refreshing if the issue persists."}
        </p>
      )}

      <div className="space-y-8">
        {templateSections.map((sec) => {
          const rows = templates.filter(
            (t) => t.goal_class === sec.goal_class && t.id !== "custom",
          )
          if (!rows.length) return null
          return (
            <section key={sec.goal_class} className="space-y-3">
              <div>
                <h3 className="text-base font-semibold tracking-tight">{sec.title}</h3>
                <p className="text-sm text-muted-foreground">{sec.description}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {rows.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => {
                      const mid = (t.default_target_amount_min + t.default_target_amount_max) / 2
                      const startToday = todayIsoLocal()
                      setDraft((d) => ({
                        ...d,
                        selected: t.id,
                        amount: Math.round(mid),
                        years: Math.round(
                          (t.default_timeframe_years_min + t.default_timeframe_years_max) / 2,
                        ),
                        recurrence_start_iso:
                          t.goal_class === "RECURRING_CASH_FLOW" ? startToday : d.recurrence_start_iso,
                      }))
                    }}
                    className={cn(
                      "text-left rounded-xl border p-4 transition-colors hover:bg-muted/50",
                      draft.selected === t.id && "ring-2 ring-primary",
                    )}
                  >
                    <div className="text-2xl mb-1" aria-hidden>
                      {t.icon}
                    </div>
                    <div className="font-medium">{t.name}</div>
                    <p className="text-xs text-muted-foreground mt-1">{templateCardFooter(t)}</p>
                  </button>
                ))}
                {sec.goal_class === "POINT_IN_TIME" && (
                  <button
                    type="button"
                    onClick={() => {
                      setDraft((d) => ({ ...d, selected: "custom", amount: 5_00_000, years: 3 }))
                    }}
                    className={cn(
                      "text-left rounded-xl border p-4 border-dashed transition-colors hover:bg-muted/50",
                      draft.selected === "custom" && "ring-2 ring-primary",
                    )}
                  >
                    <div className="text-2xl mb-1" aria-hidden>
                      ✨
                    </div>
                    <div className="font-medium">Custom</div>
                    <p className="text-xs text-muted-foreground mt-1">Define your own</p>
                  </button>
                )}
              </div>
            </section>
          )
        })}
      </div>

      {active && (
        <Card>
          <CardHeader>
            <CardTitle>
              {active.icon} {active.name}
            </CardTitle>
            <CardDescription>
              {isRecurringActive
                ? "This template is a recurring cash flow: the amount is each period (month or year), not a single lump-sum target. Copy below is planning-only."
                : "One-time goal: amounts are in rupees as of today; rough future figures use the same inflation hints as the rest of the app (for planning only)."}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 sm:max-w-md">
            <div className="space-y-2">
              <Label htmlFor="amt">
                {isRecurringActive
                  ? `Amount per ${(active.recurrence_frequency ?? "ANNUAL").toLowerCase().replaceAll("_", " ")} (today’s ₹)`
                  : "Target amount (today’s ₹)"}
              </Label>
              <Input
                id="amt"
                type="number"
                inputMode="decimal"
                min={1}
                max={GOAL_WIZARD_AMOUNT_MAX}
                step={1000}
                aria-invalid={!amountOk || !!amountParseErr}
                aria-describedby={
                  !amountOk || amountParseErr ? "amt-hint amt-err" : "amt-hint"
                }
                value={draft.amount}
                onChange={(e) => {
                  const raw = e.target.value
                  if (raw.trim() === "") {
                    setAmountParseErr(null)
                    setDraft((d) => ({ ...d, amount: 0 }))
                    return
                  }
                  const n = parseGoalDecimalString(raw)
                  if (n === null) {
                    setAmountParseErr(
                      "Use digits only (optional decimal point). Letters, scientific notation (e), and symbols are not accepted.",
                    )
                    return
                  }
                  setAmountParseErr(null)
                  setDraft((d) => ({ ...d, amount: clampAmountDuringInput(n) }))
                }}
                onBlur={() => {
                  setAmountParseErr(null)
                  setDraft((d) => ({ ...d, amount: clampAmountForDraft(d.amount) }))
                }}
              />
              <p id="amt-hint" className="text-xs text-muted-foreground">
                Between ₹1 and ₹{GOAL_WIZARD_AMOUNT_MAX.toLocaleString("en-IN")} (today’s money).
              </p>
              {(amountParseErr || !amountOk) && (
                <p id="amt-err" className="text-xs text-destructive" role="alert">
                  {amountParseErr ??
                    `Enter a target between ₹1 and ₹${GOAL_WIZARD_AMOUNT_MAX.toLocaleString("en-IN")}.`}
                </p>
              )}
            </div>
            {isRecurringActive && (
              <div className="space-y-2">
                <Label htmlFor="rec-start">Recurrence starts</Label>
                <Input
                  id="rec-start"
                  type="date"
                  className="block w-full max-w-xs"
                  value={
                    isValidIsoDate(draft.recurrence_start_iso)
                      ? draft.recurrence_start_iso
                      : todayIsoLocal()
                  }
                  onChange={(e) => {
                    const v = e.target.value
                    if (v && isValidIsoDate(v)) {
                      setDraft((d) => ({ ...d, recurrence_start_iso: v }))
                    }
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  Saved as <span className="font-mono">recurrence_start</span> on the goal (when the
                  EMI or annual budget clock starts).
                </p>
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="yrs">
                {isRecurringActive ? "Duration (years)" : "Horizon (years)"}
              </Label>
              <Input
                id="yrs"
                type="number"
                inputMode="decimal"
                min={0.25}
                max={ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS}
                step={0.5}
                aria-invalid={!yearsOk || !!yearsParseErr}
                aria-describedby={
                  !yearsOk || yearsParseErr ? "yrs-hint yrs-err" : "yrs-hint"
                }
                value={draft.years}
                onChange={(e) => {
                  const raw = e.target.value
                  if (raw.trim() === "") {
                    setYearsParseErr(null)
                    setDraft((d) => ({ ...d, years: 0 }))
                    return
                  }
                  const n = parseGoalDecimalString(raw)
                  if (n === null) {
                    setYearsParseErr(
                      "Use digits only (optional decimal). Scientific notation and stray characters are not accepted.",
                    )
                    return
                  }
                  setYearsParseErr(null)
                  setDraft((d) => ({ ...d, years: clampYearsDuringInput(n) }))
                }}
                onBlur={() => {
                  setYearsParseErr(null)
                  setDraft((d) => ({ ...d, years: clampYearsForDraft(d.years) }))
                }}
              />
              <p id="yrs-hint" className="text-xs text-muted-foreground">
                {isRecurringActive
                  ? `How long this recurring line runs (${0.25}–${ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS} years). We add this many calendar months to the start date to get the end date below.`
                  : `Planning horizon supported here: ${0.25}–${ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS} years (about 3 months to a long career-length goal).`}
              </p>
              {(yearsParseErr || !yearsOk) && (
                <p id="yrs-err" className="text-xs text-destructive" role="alert">
                  {yearsParseErr ??
                    `Enter between 0.25 and ${ONBOARDING_GOAL_TEMPLATES_API_MAX_YEARS} years. Very large values are not supported here because the preview date must stay within a normal planning range.`}
                </p>
              )}
            </div>
            <div className="text-xs text-muted-foreground space-y-1">
              {isRecurringActive && recurringEndIso ? (
                <>
                  <p>
                    <span className="font-mono">recurrence_end</span> (start + duration):{" "}
                    <span className="font-mono">{recurringEndIso}</span>
                  </p>
                  <p>
                    <span className="font-mono">target_date</span> (same book-end for this wizard):{" "}
                    <span className="font-mono">{recurringEndIso}</span>
                  </p>
                  <p className="text-muted-foreground/90">
                    You can tweak dates later in Goals; the API stores optional{" "}
                    <span className="font-mono">recurrence_end</span> — we set it from your duration
                    so simulations see a clear window.
                  </p>
                </>
              ) : (
                <p>
                  Suggested <span className="font-mono">target_date</span> (from today + horizon,
                  edit later in Goals): <span className="font-mono">{pitTargetDateIso}</span>
                </p>
              )}
            </div>
          </CardContent>
          {preview && (
            <div className="px-6 pb-2 text-sm text-muted-foreground border-t border-border/50 pt-4">
              {preview.copy}
            </div>
          )}
          <CardFooter>
            <Button
              type="button"
              disabled={isPending || !canCreateGoal}
              onClick={() => {
                if (!active || !canCreateGoal) return
                const body = buildCreatePayload(active, draft, pitTargetDateIso)
                create(body, {
                  onSuccess: () => {
                    setDone(true)
                    clearDraft()
                  },
                })
              }}
            >
              {isPending ? "Saving…" : "Create goal"}
            </Button>
            {done && (
              <p className="ml-3 text-sm text-emerald-600" role="status">
                Added — you can continue in Goals for fine-tuning.
              </p>
            )}
            {isError && (
              <p className="ml-3 text-sm text-destructive" role="alert">
                {getUserFacingErrorMessage(error) || "Couldn't create that goal. Try again."}
              </p>
            )}
          </CardFooter>
        </Card>
      )}

      {draft.selected == null && (extraHeadline || extraHeadlineRecurring) && (
        <div className="text-xs text-muted-foreground border-t pt-3 space-y-2">
          {extraHeadline && <p>{extraHeadline.copy}</p>}
          {extraHeadlineRecurring && <p>{extraHeadlineRecurring.copy}</p>}
        </div>
      )}

      <section
        className="border-t border-border/60 pt-6 space-y-3"
        aria-labelledby="saved-goals-heading"
      >
        <h3 id="saved-goals-heading" className="text-base font-semibold tracking-tight">
          Your saved goals
        </h3>
        <p className="text-xs text-muted-foreground">
          Same list as the main Goals page — shown here on setup so new goals appear right
          after you save (below).
        </p>
        {savedGoalsLoading && (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" />
            Loading your goals…
          </p>
        )}
        {!savedGoalsLoading && (!savedGoals || savedGoals.length === 0) && (
          <p className="text-sm text-muted-foreground">
            No goals in your account yet — create one with the templates above.
          </p>
        )}
        {savedGoals && savedGoals.length > 0 && (
          <ul className="space-y-2">
            {savedGoals.map((g) => {
              const isRecurring = (g.goal_class ?? "").toUpperCase() === "RECURRING_CASH_FLOW"
              const isDeleting = deletingGoalId === g.id
              return (
                <li
                  key={g.id}
                  className="rounded-lg border border-border/80 bg-card px-3 py-2.5 text-sm"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 space-y-1 flex-1">
                      <div className="font-medium text-foreground">{g.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {isRecurring ? (
                          <>
                            Recurring —{" "}
                            {formatCurrency(Number(g.recurrence_amount) || 0)} per{" "}
                            {(g.recurrence_frequency ?? "MONTHLY").toLowerCase()}
                            {g.recurrence_start ? (
                              <>
                                {" "}
                                · start <span className="font-mono">{g.recurrence_start}</span>
                              </>
                            ) : null}
                            {(g.recurrence_end ?? g.target_date) ? (
                              <>
                                {" "}
                                · end{" "}
                                <span className="font-mono">{g.recurrence_end ?? g.target_date}</span>
                              </>
                            ) : null}
                          </>
                        ) : (
                          <>
                            One-time — target{" "}
                            {g.target_amount != null ? formatCurrency(g.target_amount) : "—"}
                            {g.target_date ? (
                              <>
                                {" "}
                                · due <span className="font-mono">{g.target_date}</span>
                              </>
                            ) : null}
                          </>
                        )}
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-8 shrink-0 text-muted-foreground hover:text-destructive"
                      disabled={isDeleting}
                      aria-label={`Delete goal: ${g.name}`}
                      onClick={() => {
                        setDeletingGoalId(g.id)
                        deleteGoal(g.id, {
                          onSettled: () => setDeletingGoalId(null),
                        })
                      }}
                    >
                      {isDeleting ? (
                        <Loader2 className="size-4 animate-spin" aria-hidden />
                      ) : (
                        <Trash2 className="size-4" aria-hidden />
                      )}
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
