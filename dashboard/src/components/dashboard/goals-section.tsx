/**
 * GoalsSection — lists **all** saved goals with basic fields: subtype (“type”),
 * targets / recurrence amounts, dates, and progress. Completed goals appear in a
 * short section at the bottom (still ordered for allocation priority above).
 *
 * The sheet uses `goal_class`-style choices (one-time / investment lump sum, recurring, spending cap);
 * the client maps each choice to API `goal_type` + `goal_class` on create/update.
 */

"use client"

import * as React from "react"
import {
  Car,
  CheckCircle2,
  Clock,
  CreditCard,
  GraduationCap,
  Heart,
  Home,
  Landmark,
  Pencil,
  Plane,
  Plus,
  Repeat,
  Shield,
  Target,
  TrendingDown,
  Trash2,
} from "lucide-react"
import posthog from "posthog-js"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateGoal,
  useDeleteGoal,
  useGoals,
  useUpdateGoal,
} from "@/hooks/use-goals"
import {
  GOAL_UI_KIND_LABELS,
  addGoalFormToCreatePayload,
  defaultAddGoalForm,
  inferGoalUiKind,
  labelGoalUiKind,
  prefillAddFormForChartKey,
  type AddGoalFormState,
  type GoalUiKind,
} from "@/lib/goal-ui-kind"
import {
  GoalTargetMoneyCardLine,
  GoalTargetMoneyHint,
} from "@/components/goal-target-money-hint"
import { isDemoMode } from "@/lib/demo"
import { inflationSelectLabelForSubtype, previewInflationResolutionForForm } from "@/lib/goal-inflation-preview"
import {
  DEFAULT_HEADLINE_INFLATION_PCT,
  MIN_MONTHLY_GOAL_CONTRIBUTION_INR,
  recurrenceAmountToMonthlyInr,
} from "@/lib/goal-target-money"
import { sanitizeHtmlDateInputValue } from "@/lib/onboarding-input-validation"
import {
  cn,
  formatCurrency,
  formatInrMoneyInput,
  parseInrMoneyInput,
  reformatInrMoneyTyping,
} from "@/lib/utils"
import type { Goal, GoalUpdate } from "@/lib/types"

/** Optional subtype for recurring goals — must match api/routes/goals.py _VALID_GOAL_SUBTYPES. */
const GOAL_SUBTYPE_OPTIONS = [
  { value: "", label: "None" },
  { value: "LOAN_PAYOFF", label: "Loan payoff" },
  { value: "HOME_PURCHASE", label: "Home purchase" },
  { value: "VEHICLE", label: "Vehicle" },
  { value: "RETIREMENT", label: "Retirement" },
  { value: "CHILD_EDUCATION", label: "Child education" },
  { value: "EMERGENCY_FUND", label: "Emergency fund" },
  { value: "WEDDING", label: "Wedding" },
  { value: "TRAVEL", label: "Travel" },
  { value: "CUSTOM", label: "Custom" },
] as const

/** Subtypes used for inflation mapping on create (one-time & growth) — no “none”; defaults to CUSTOM. */
const GOAL_SUBTYPE_OPTIONS_INFLATION = GOAL_SUBTYPE_OPTIONS.filter((o) => o.value !== "") as {
  value: string
  label: string
}[]

function labelForStoredGoalSubtype(goalSubtype: string | null | undefined): string {
  const key = (goalSubtype ?? "").trim().toUpperCase() || "CUSTOM"
  const hit = GOAL_SUBTYPE_OPTIONS_INFLATION.find((o) => o.value === key)
  return hit?.label ?? key.replace(/_/g, " ").toLowerCase()
}

/** Human hint from today → target date (one-time goals). */
function formatHorizonToTargetDate(iso: string | null | undefined): string | null {
  if (!iso?.trim()) return null
  const end = new Date(`${iso.trim()}T12:00:00`)
  if (Number.isNaN(end.getTime())) return null
  const ms = end.getTime() - Date.now()
  if (ms <= 0) return "due or past"
  const days = Math.ceil(ms / 86_400_000)
  if (days < 60) return `${days} day${days === 1 ? "" : "s"} left`
  const mo = Math.round(days / 30.44)
  if (mo < 24) return `~${mo} mo left`
  const yr = (days / 365.25).toFixed(1)
  return `~${yr} yr left`
}

function recurrenceFrequencyLabel(freq: string | null | undefined): string {
  const u = (freq ?? "MONTHLY").toUpperCase()
  if (u === "MONTHLY") return "month"
  if (u === "QUARTERLY") return "quarter"
  if (u === "ANNUAL") return "year"
  return u.toLowerCase().replaceAll("_", " ")
}

/** Second line(s) under the goal name — PIT vs recurring vs cap. */
function GoalBasicDetails({ goal, uiKind }: { goal: Goal; uiKind: GoalUiKind }) {
  if (uiKind === "RECURRING_CASH_FLOW") {
    const period = recurrenceFrequencyLabel(goal.recurrence_frequency)
    const endLabel = goal.recurrence_end ?? goal.target_date ?? null
    return (
      <dl className="mt-1 grid gap-0.5 text-xs text-muted-foreground">
        <div className="flex flex-wrap gap-x-2 gap-y-0">
          <dt className="sr-only">Category</dt>
          <dd>Type: {labelForStoredGoalSubtype(goal.goal_subtype)}</dd>
        </div>
        {goal.recurrence_amount != null && goal.recurrence_amount > 0 && (
          <div className="flex flex-wrap gap-x-2 gap-y-0">
            <dt className="sr-only">Amount</dt>
            <dd>
              Amount: {formatCurrency(goal.recurrence_amount)} / {period}
            </dd>
          </div>
        )}
        <div className="flex flex-wrap gap-x-2 gap-y-0">
          <dt className="sr-only">Window</dt>
          <dd>
            {goal.recurrence_start ? (
              <>
                Start: <span className="font-mono text-foreground/80">{goal.recurrence_start}</span>
                {" · "}
              </>
            ) : (
              "Start: — · "
            )}
            End:{" "}
            {endLabel ? (
              <span className="font-mono text-foreground/80">{endLabel}</span>
            ) : (
              "— (open-ended)"
            )}
          </dd>
        </div>
      </dl>
    )
  }

  if (uiKind === "POINT_IN_TIME") {
    const horizon = goal.target_date ? formatHorizonToTargetDate(goal.target_date) : null
    return (
      <dl className="mt-1 grid gap-0.5 text-xs text-muted-foreground">
        <div className="flex flex-wrap gap-x-2 gap-y-0">
          <dt className="sr-only">Category</dt>
          <dd>Type: {labelForStoredGoalSubtype(goal.goal_subtype)}</dd>
        </div>
        {goal.target_amount != null && goal.target_amount > 0 && (
          <div className="flex flex-wrap gap-x-2 gap-y-0">
            <dt className="sr-only">Target</dt>
            <dd>Target: {formatCurrency(goal.target_amount)} (today&apos;s ₹)</dd>
          </div>
        )}
        {goal.target_date ? (
          <div className="flex flex-wrap gap-x-2 gap-y-0">
            <dt className="sr-only">Due</dt>
            <dd>
              Due: <span className="font-mono text-foreground/80">{goal.target_date}</span>
              {horizon ? <> ({horizon})</> : null}
            </dd>
          </div>
        ) : (
          <div>No deadline set</div>
        )}
      </dl>
    )
  }

  if (uiKind === "EXPENSE_LIMIT") {
    return (
      <dl className="mt-1 text-xs text-muted-foreground">
        <dd>
          Cap:{" "}
          {goal.target_amount != null ? formatCurrency(goal.target_amount) : "—"} · Window:{" "}
          {(goal.progress_cadence ?? "MONTHLY").toLowerCase()}
        </dd>
      </dl>
    )
  }

  return null
}

/** Stable ordering: allocation priority (if set), then wizard priority, then id. */
function sortGoalsForDisplay(goals: Goal[]): Goal[] {
  return [...goals].sort((a, b) => {
    const pa = a.allocation_priority ?? a.priority ?? 99
    const pb = b.allocation_priority ?? b.priority ?? 99
    if (pa !== pb) return pa - pb
    return a.id - b.id
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// Visual helpers — icons, colours, stat blocks
// ─────────────────────────────────────────────────────────────────────────────

function GoalSubtypeIcon({ subtype, uiKind }: { subtype: string | null | undefined; uiKind: GoalUiKind }) {
  if (uiKind === "EXPENSE_LIMIT") return <TrendingDown className="size-4" />
  if (uiKind === "RECURRING_CASH_FLOW") return <Repeat className="size-4" />
  const key = (subtype ?? "").toUpperCase()
  const icons: Record<string, React.ReactNode> = {
    RETIREMENT: <Landmark className="size-4" />,
    HOME_PURCHASE: <Home className="size-4" />,
    VEHICLE: <Car className="size-4" />,
    CHILD_EDUCATION: <GraduationCap className="size-4" />,
    EMERGENCY_FUND: <Shield className="size-4" />,
    WEDDING: <Heart className="size-4" />,
    TRAVEL: <Plane className="size-4" />,
    LOAN_PAYOFF: <CreditCard className="size-4" />,
  }
  return <>{icons[key] ?? <Target className="size-4" />}</>
}

function goalSubtypeBg(subtype: string | null | undefined, uiKind: GoalUiKind): string {
  if (uiKind === "EXPENSE_LIMIT") return "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400"
  if (uiKind === "RECURRING_CASH_FLOW") return "bg-violet-100 text-violet-600 dark:bg-violet-900/30 dark:text-violet-400"
  const key = (subtype ?? "").toUpperCase()
  const map: Record<string, string> = {
    RETIREMENT: "bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400",
    HOME_PURCHASE: "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400",
    VEHICLE: "bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400",
    CHILD_EDUCATION: "bg-violet-100 text-violet-600 dark:bg-violet-900/30 dark:text-violet-400",
    EMERGENCY_FUND: "bg-sky-100 text-sky-600 dark:bg-sky-900/30 dark:text-sky-400",
    WEDDING: "bg-pink-100 text-pink-600 dark:bg-pink-900/30 dark:text-pink-400",
    TRAVEL: "bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400",
    LOAN_PAYOFF: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  }
  return map[key] ?? "bg-muted text-muted-foreground"
}

function progressTextColor(pct: number, mode: "expense" | "savings"): string {
  if (mode === "expense") {
    if (pct >= 100) return "text-red-600 dark:text-red-400"
    if (pct >= 85) return "text-amber-600 dark:text-amber-400"
    return "text-green-600 dark:text-green-400"
  }
  if (pct >= 100) return "text-blue-600 dark:text-blue-400"
  if (pct >= 90) return "text-green-600 dark:text-green-400"
  if (pct >= 60) return "text-amber-600 dark:text-amber-400"
  return "text-red-600 dark:text-red-400"
}

function StatItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5 truncate">{label}</p>
      <p className="text-xs font-semibold text-foreground truncate">{value}</p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Progress % display (thresholds are display-only; simulation returns authoritative %)
// ─────────────────────────────────────────────────────────────────────────────

function progressBadgeClass(pct: number, mode: "expense" | "savings"): string {
  if (mode === "expense") {
    if (pct >= 100) {
      return "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400"
    }
    if (pct >= 85) {
      return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400"
    }
    return "border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-400"
  }
  if (pct >= 100) {
    return "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400"
  }
  if (pct >= 90) {
    return "border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-400"
  }
  if (pct >= 60) {
    return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400"
  }
  return "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400"
}

function progressBarClass(pct: number, mode: "expense" | "savings"): string {
  if (mode === "expense") {
    if (pct >= 100) return "[&>div]:bg-red-500"
    if (pct >= 85) return "[&>div]:bg-amber-500"
    return "[&>div]:bg-green-500"
  }
  if (pct >= 100) return "[&>div]:bg-blue-500"
  if (pct >= 90) return "[&>div]:bg-green-500"
  if (pct >= 60) return "[&>div]:bg-amber-500"
  return "[&>div]:bg-red-500"
}

// ─────────────────────────────────────────────────────────────────────────────
// EditGoalSheet — fields depend on inferred goal “kind” (goal_class + legacy goal_type)
// ─────────────────────────────────────────────────────────────────────────────

function EditGoalSheet({ goal }: { goal: Goal }) {
  const [open, setOpen] = React.useState(false)
  const [recurringAmountError, setRecurringAmountError] = React.useState<string | null>(null)
  const { mutate: patchGoal, isPending } = useUpdateGoal()
  const uiKind = inferGoalUiKind(goal)

  const [name, setName] = React.useState(goal.name)
  const [targetAmount, setTargetAmount] = React.useState(
    goal.target_amount != null ? formatInrMoneyInput(goal.target_amount) : "",
  )
  const [targetDate, setTargetDate] = React.useState(goal.target_date ?? "")
  const [notes, setNotes] = React.useState(goal.notes ?? "")
  const [startingBalance, setStartingBalance] = React.useState(
    goal.starting_balance != null ? formatInrMoneyInput(goal.starting_balance) : "",
  )
  const [goalInflation, setGoalInflation] = React.useState(
    goal.goal_specific_inflation_rate != null ? String(goal.goal_specific_inflation_rate) : "",
  )
  const [expectedReturn, setExpectedReturn] = React.useState(
    goal.expected_return_rate != null ? String(goal.expected_return_rate) : "",
  )
  const [recurrenceAmount, setRecurrenceAmount] = React.useState(
    goal.recurrence_amount != null ? formatInrMoneyInput(goal.recurrence_amount) : "",
  )
  const [recurrenceFrequency, setRecurrenceFrequency] = React.useState(
    (goal.recurrence_frequency as AddGoalFormState["recurrence_frequency"]) ?? "MONTHLY",
  )
  const [recurrenceStart, setRecurrenceStart] = React.useState(goal.recurrence_start ?? "")
  const [recurrenceEnd, setRecurrenceEnd] = React.useState(goal.recurrence_end ?? "")
  const [progressCadence, setProgressCadence] = React.useState(
    (goal.progress_cadence as "MONTHLY" | "ANNUAL") ?? "MONTHLY",
  )

  React.useEffect(() => {
    if (!open) return
    setName(goal.name)
    setTargetAmount(goal.target_amount != null ? formatInrMoneyInput(goal.target_amount) : "")
    setTargetDate(goal.target_date ?? "")
    setNotes(goal.notes ?? "")
    setStartingBalance(goal.starting_balance != null ? formatInrMoneyInput(goal.starting_balance) : "")
    setGoalInflation(
      goal.goal_specific_inflation_rate != null ? String(goal.goal_specific_inflation_rate) : "",
    )
    setExpectedReturn(goal.expected_return_rate != null ? String(goal.expected_return_rate) : "")
    setRecurrenceAmount(goal.recurrence_amount != null ? formatInrMoneyInput(goal.recurrence_amount) : "")
    setRecurrenceFrequency(
      (goal.recurrence_frequency as AddGoalFormState["recurrence_frequency"]) ?? "MONTHLY",
    )
    setRecurrenceStart(goal.recurrence_start ?? "")
    setRecurrenceEnd(goal.recurrence_end ?? "")
    setProgressCadence((goal.progress_cadence as "MONTHLY" | "ANNUAL") ?? "MONTHLY")
  }, [open, goal])

  const isExpenseLimit = uiKind === "EXPENSE_LIMIT"

  function parseOptFloat(s: string): number | null | undefined {
    const t = s.trim()
    if (t === "") return undefined
    const n = parseFloat(t)
    return Number.isNaN(n) ? undefined : n
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (isDemoMode) return
    if (!name.trim()) return

    setRecurringAmountError(null)

    if (uiKind === "RECURRING_CASH_FLOW") {
      const ra = parseInrMoneyInput(recurrenceAmount)
      if (ra == null || ra <= 0) {
        setRecurringAmountError("Enter a positive amount per period.")
        return
      }
      const monthly = recurrenceAmountToMonthlyInr(ra, recurrenceFrequency)
      if (monthly < MIN_MONTHLY_GOAL_CONTRIBUTION_INR) {
        setRecurringAmountError(
          `Monthly cash flow must be at least ₹${MIN_MONTHLY_GOAL_CONTRIBUTION_INR.toLocaleString("en-IN")} in today's money (or the simulator treats it as zero).`,
        )
        return
      }
    }

    const update: GoalUpdate = {
      name: name.trim(),
      notes: notes.trim() ? notes.trim() : null,
    }

    if (uiKind !== "RECURRING_CASH_FLOW") {
      update.target_date = targetDate.trim() ? targetDate.trim() : null
      if (targetAmount.trim() === "") {
        update.target_amount = null
      } else {
        const n = parseInrMoneyInput(targetAmount)
        if (n != null) update.target_amount = n
      }
    }

    if (uiKind === "POINT_IN_TIME") {
      const sb = parseInrMoneyInput(startingBalance)
      if (sb != null) {
        update.starting_balance = sb
        update.current_value = sb
      }
      const inf = parseOptFloat(goalInflation)
      if (inf !== undefined) update.goal_specific_inflation_rate = inf
      const er = parseOptFloat(expectedReturn)
      if (er !== undefined) update.expected_return_rate = er
    }
    if (uiKind === "RECURRING_CASH_FLOW") {
      const ra = parseInrMoneyInput(recurrenceAmount)
      if (ra != null) update.recurrence_amount = ra
      update.recurrence_frequency = recurrenceFrequency
      update.recurrence_start = recurrenceStart.trim() ? recurrenceStart.trim() : null
      update.recurrence_end = recurrenceEnd.trim() ? recurrenceEnd.trim() : null
    }
    if (uiKind === "EXPENSE_LIMIT") {
      update.progress_cadence = progressCadence
    }

    patchGoal(
      { id: goal.id, update },
      { onSuccess: () => setOpen(false) },
    )
  }

  function handleEditSheetOpenChange(next: boolean) {
    if (next) {
      posthog.capture("goal_card_opened", { action: "edit", goal_type: goal.goal_type })
    }
    setOpen(next)
  }

  return (
    <Sheet open={open} onOpenChange={handleEditSheetOpenChange}>
      <SheetTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            className="size-6 text-muted-foreground hover:text-foreground"
            aria-label={`Edit goal: ${goal.name}`}
            type="button"
          >
            <Pencil className="size-3" />
          </Button>
        }
      />
      <SheetContent className="flex h-full w-[360px] flex-col sm:w-[400px]">
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 pb-10 pt-5">
          <SheetHeader className="shrink-0 space-y-2 p-0 pr-12 pb-4">
            <SheetTitle>Edit goal</SheetTitle>
            <SheetDescription>
              {" "}
              <span className="font-medium text-foreground">
                {labelGoalUiKind(uiKind)}
              </span>
              . To switch to a different kind, delete this goal and create a new one.
            </SheetDescription>
          </SheetHeader>

          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor={`edit-goal-name-${goal.id}`}>Goal name</Label>
              <Input
                id={`edit-goal-name-${goal.id}`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>

            {(uiKind === "POINT_IN_TIME" || uiKind === "RECURRING_CASH_FLOW") && (
              <div className="flex flex-col gap-1.5 rounded-md border border-border bg-muted/20 px-3 py-2.5">
                <p className="text-xs font-medium text-foreground">Category (inflation)</p>
                <p className="text-sm text-muted-foreground">{labelForStoredGoalSubtype(goal.goal_subtype)}</p>
                <p className="text-[11px] text-muted-foreground leading-snug">
                  This was chosen when the goal was created. It cannot be changed so inflation stays
                  consistent over time.
                </p>
              </div>
            )}

            {uiKind !== "RECURRING_CASH_FLOW" ? (
              <div className="grid grid-cols-2 gap-4">
                <div className="flex flex-col gap-2">
                  <Label htmlFor={`edit-goal-target-${goal.id}`}>
                    {isExpenseLimit ? "Cap / limit (₹)" : "Target (₹, today's money)"}
                  </Label>
                  <Input
                    id={`edit-goal-target-${goal.id}`}
                    type="text"
                    inputMode="decimal"
                    autoComplete="off"
                    placeholder="e.g. 10,000"
                    className="tabular-nums"
                    value={targetAmount}
                    onChange={(e) => setTargetAmount(reformatInrMoneyTyping(e.target.value))}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor={`edit-goal-date-${goal.id}`}>Deadline (optional)</Label>
                  <Input
                    id={`edit-goal-date-${goal.id}`}
                    type="date"
                    min="1900-01-01"
                    max="9999-12-31"
                    value={targetDate}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") {
                        setTargetDate("")
                        return
                      }
                      const v = sanitizeHtmlDateInputValue(raw)
                      if (v != null) setTargetDate(v)
                    }}
                  />
                </div>
              </div>
            ) : null}

            {uiKind === "POINT_IN_TIME" && !isExpenseLimit ? (
              <GoalTargetMoneyHint
                rawTargetInput={targetAmount}
                targetDate={targetDate}
                goalSpecificInflationInput={goalInflation}
                inflationResolution={goal.inflation_resolution ?? null}
              />
            ) : null}

            {uiKind === "POINT_IN_TIME" && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={`edit-start-${goal.id}`}>Already saved / corpus (₹)</Label>
                    <Input
                      id={`edit-start-${goal.id}`}
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      placeholder="0"
                      className="tabular-nums"
                      value={startingBalance}
                      onChange={(e) => setStartingBalance(reformatInrMoneyTyping(e.target.value))}
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={`edit-er-${goal.id}`}>Expected return % (optional)</Label>
                    <Input
                      id={`edit-er-${goal.id}`}
                      type="number"
                      step="0.1"
                      placeholder="e.g. 10"
                      value={expectedReturn}
                      onChange={(e) => setExpectedReturn(e.target.value)}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor={`edit-infl-${goal.id}`}>Goal inflation % (optional)</Label>
                  <Input
                    id={`edit-infl-${goal.id}`}
                    type="number"
                    step="0.1"
                    placeholder="e.g. 6"
                    value={goalInflation}
                    onChange={(e) => setGoalInflation(e.target.value)}
                  />
                </div>
              </>
            )}

            {uiKind === "RECURRING_CASH_FLOW" && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={`edit-ramt-${goal.id}`}>
                      {"Amount per period (₹, today's money)"}
                    </Label>
                    <Input
                      id={`edit-ramt-${goal.id}`}
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      required
                      className="tabular-nums"
                      aria-invalid={recurringAmountError ? true : undefined}
                      value={recurrenceAmount}
                      onChange={(e) => {
                        setRecurrenceAmount(reformatInrMoneyTyping(e.target.value))
                        setRecurringAmountError(null)
                      }}
                    />
                    {recurringAmountError ? (
                      <p className="text-xs text-destructive" role="alert">
                        {recurringAmountError}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label>Frequency</Label>
                    <Select
                      value={recurrenceFrequency}
                      onValueChange={(v) =>
                        setRecurrenceFrequency(v as AddGoalFormState["recurrence_frequency"])
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="MONTHLY">Monthly</SelectItem>
                        <SelectItem value="QUARTERLY">Quarterly</SelectItem>
                        <SelectItem value="ANNUAL">Annual</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={`edit-rs-${goal.id}`}>First payment (start)</Label>
                    <Input
                      id={`edit-rs-${goal.id}`}
                      type="date"
                      min="1900-01-01"
                      max="9999-12-31"
                      required
                      value={recurrenceStart}
                      onChange={(e) => {
                        const raw = e.target.value
                        if (raw === "") {
                          setRecurrenceStart("")
                          return
                        }
                        const v = sanitizeHtmlDateInputValue(raw)
                        if (v != null) setRecurrenceStart(v)
                      }}
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={`edit-re-${goal.id}`}>Last payment (optional)</Label>
                    <Input
                      id={`edit-re-${goal.id}`}
                      type="date"
                      min="1900-01-01"
                      max="9999-12-31"
                      value={recurrenceEnd}
                      onChange={(e) => {
                        const raw = e.target.value
                        if (raw === "") {
                          setRecurrenceEnd("")
                          return
                        }
                        const v = sanitizeHtmlDateInputValue(raw)
                        if (v != null) setRecurrenceEnd(v)
                      }}
                    />
                  </div>
                </div>
              </>
            )}

            {uiKind === "EXPENSE_LIMIT" && (
              <div className="flex flex-col gap-2">
                <Label>Progress window</Label>
                <Select
                  value={progressCadence}
                  onValueChange={(v) => setProgressCadence(v as "MONTHLY" | "ANNUAL")}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="MONTHLY">Monthly (vs cap this month)</SelectItem>
                    <SelectItem value="ANNUAL">Annual (YTD vs cap)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="flex flex-col gap-2">
              <Label htmlFor={`edit-goal-notes-${goal.id}`}>Notes (optional)</Label>
              <Textarea
                id={`edit-goal-notes-${goal.id}`}
                rows={3}
                placeholder="Context, reminders, why this goal exists…"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </div>

            <Button
              type="submit"
              className="mt-1 w-full"
              disabled={isPending || isDemoMode}
              title={
                isDemoMode
                  ? "Demo mode — sample goals are view-only; saving edits is turned off."
                  : undefined
              }
            >
              {isPending ? "Saving…" : "Save changes"}
            </Button>
          </form>
        </div>
      </SheetContent>
    </Sheet>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// GoalCard — single goal row
// ─────────────────────────────────────────────────────────────────────────────

function GoalCard({ goal }: { goal: Goal }) {
  const { mutate: updateGoal } = useUpdateGoal()
  const { mutate: deleteGoal } = useDeleteGoal()
  const [editingValue, setEditingValue] = React.useState(false)
  const [newValue, setNewValue] = React.useState(
    goal.current_value != null ? formatInrMoneyInput(goal.current_value) : "",
  )

  const uiKind = inferGoalUiKind(goal)
  const isExpenseLimit = uiKind === "EXPENSE_LIMIT"
  const pct = goal.computed_percentage
  const progressValue = Math.min(pct, 100)
  const badgeMode = isExpenseLimit ? "expense" : "savings"
  const horizonLabel = goal.target_date ? formatHorizonToTargetDate(goal.target_date) : null

  function handleValueSave() {
    const val = parseInrMoneyInput(newValue)
    if (val != null) {
      updateGoal({ id: goal.id, update: { current_value: val } })
    }
    setEditingValue(false)
  }

  return (
    <div className="rounded-xl border bg-card p-5 flex flex-col gap-4 hover:border-border/80 transition-colors group">

      {/* ── Header: icon + name + actions ── */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <div className={cn(
            "size-10 rounded-xl flex items-center justify-center shrink-0",
            goalSubtypeBg(goal.goal_subtype, uiKind)
          )}>
            <GoalSubtypeIcon subtype={goal.goal_subtype} uiKind={uiKind} />
          </div>
          <div className="min-w-0 flex-1 pt-0.5">
            <p className="font-semibold text-sm leading-tight truncate">{goal.name}</p>
            <p className="text-xs text-muted-foreground mt-0.5 truncate">
              {labelGoalUiKind(uiKind)}
              {goal.goal_subtype && uiKind !== "EXPENSE_LIMIT"
                ? ` · ${labelForStoredGoalSubtype(goal.goal_subtype)}`
                : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          <EditGoalSheet goal={goal} />
          <Button
            variant="ghost"
            size="icon"
            className="size-6 text-muted-foreground hover:text-destructive"
            aria-label={`Delete goal: ${goal.name}`}
            onClick={() => deleteGoal(goal.id)}
          >
            <Trash2 className="size-3" />
          </Button>
        </div>
      </div>

      {/* ── Progress: big % + bar ── */}
      <div className="space-y-2.5">
        <div className="flex items-baseline justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <span className={cn(
              "text-3xl font-bold tracking-tight tabular-nums",
              progressTextColor(pct, badgeMode)
            )}>
              {Math.round(pct)}%
            </span>
            {pct >= 100 && !isExpenseLimit && (
              <CheckCircle2 className="size-4 text-blue-500 self-center shrink-0" />
            )}
          </div>
          <span className="text-xs text-muted-foreground">
            {isExpenseLimit ? "of cap" : "funded"}
          </span>
        </div>
        <Progress
          value={progressValue}
          className={cn("h-2 rounded-full", progressBarClass(pct, badgeMode))}
        />
      </div>

      {/* ── Stats grid ── */}
      <div className="grid grid-cols-3 gap-3 border-t border-border/40 pt-3">
        {uiKind === "POINT_IN_TIME" && (
          <>
            <StatItem label="Saved" value={formatCurrency(goal.computed_current_value)} />
            {goal.target_amount != null && goal.target_amount > 0
              ? <StatItem label="Target" value={formatCurrency(goal.target_amount)} />
              : <div />}
            {horizonLabel ? (
              <StatItem
                label="Left"
                value={
                  <span className="flex items-center gap-1">
                    <Clock className="size-2.5 text-muted-foreground shrink-0" />
                    {horizonLabel}
                  </span>
                }
              />
            ) : <div />}
          </>
        )}
        {uiKind === "RECURRING_CASH_FLOW" && (
          <>
            {goal.recurrence_amount != null && goal.recurrence_amount > 0
              ? <StatItem
                  label="Per period"
                  value={`${formatCurrency(goal.recurrence_amount)}/${recurrenceFrequencyLabel(goal.recurrence_frequency)}`}
                />
              : <div />}
            <StatItem label="From" value={goal.recurrence_start ?? "—"} />
            <StatItem label="Until" value={goal.recurrence_end ?? "Open"} />
          </>
        )}
        {uiKind === "EXPENSE_LIMIT" && (
          <>
            <StatItem label="Spent" value={formatCurrency(goal.computed_current_value)} />
            <StatItem label="Cap" value={goal.target_amount ? formatCurrency(goal.target_amount) : "—"} />
            <StatItem label="Window" value={(goal.progress_cadence ?? "MONTHLY").toLowerCase()} />
          </>
        )}
      </div>

      {/* ── Inflation planning hint ── */}
      {uiKind === "POINT_IN_TIME" &&
        !isExpenseLimit &&
        goal.target_amount != null &&
        goal.target_amount > 0 && (
          <GoalTargetMoneyCardLine
            rawTarget={goal.target_amount}
            targetDate={goal.target_date}
            goalSpecificInflation={goal.goal_specific_inflation_rate}
            inflationResolution={goal.inflation_resolution ?? null}
          />
        )}

      {/* ── Notes ── */}
      {goal.notes && (
        <p className="text-xs text-muted-foreground border-t border-border/50 pt-3 whitespace-pre-wrap">
          {goal.notes}
        </p>
      )}

      {/* ── Update progress ── */}
      {!isExpenseLimit && (
        <div className="flex items-center gap-2 border-t border-border/40 pt-3 -mb-1">
          {editingValue ? (
            <>
              <Input
                type="text"
                inputMode="decimal"
                autoComplete="off"
                value={newValue}
                onChange={(e) => setNewValue(reformatInrMoneyTyping(e.target.value))}
                className="h-7 text-xs w-28 tabular-nums"
                onKeyDown={(e) => { if (e.key === "Enter") handleValueSave() }}
                autoFocus
              />
              <Button size="sm" className="h-7 text-xs" onClick={handleValueSave}>Save</Button>
              <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setEditingValue(false)}>Cancel</Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs text-muted-foreground px-0 hover:text-foreground"
              onClick={() => {
                setNewValue(
                  goal.current_value != null ? formatInrMoneyInput(goal.current_value) : "",
                )
                setEditingValue(true)
              }}
            >
              Update progress
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

/** Inline validation for Add Goal — recurring requires amount + first payment date. */
type AddGoalFieldErrors = {
  name?: string
  recurrence_amount?: string
  recurrence_start?: string
}

// ─────────────────────────────────────────────────────────────────────────────
// AddGoalSheet — slide-in form for creating a new goal
// ─────────────────────────────────────────────────────────────────────────────

function AddGoalSheet({ prefillChartKey }: { prefillChartKey?: string | null }) {
  const [open, setOpen] = React.useState(false)
  const { mutate: create, isPending } = useCreateGoal()

  const [form, setForm] = React.useState<AddGoalFormState>(() => defaultAddGoalForm())
  const [fieldErrors, setFieldErrors] = React.useState<AddGoalFieldErrors>({})

  function resetAddForm() {
    setForm(defaultAddGoalForm())
    setFieldErrors({})
  }

  function handleOpenChange(next: boolean) {
    // Demo uses a fixed seeded goal set — do not open the create sheet from the trigger or elsewhere.
    if (next && isDemoMode) return
    setOpen(next)
    if (next) {
      posthog.capture("goal_card_opened", { action: "new" })
      const base = defaultAddGoalForm()
      const pre = prefillAddFormForChartKey(prefillChartKey)
      setForm({ ...base, ...pre })
      setFieldErrors({})
    }
    if (!next) resetAddForm()
  }

  function setField<K extends keyof AddGoalFormState>(key: K, value: AddGoalFormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  /** Clear the inline error for a field once the user edits it. */
  function clearFieldError(key: keyof AddGoalFieldErrors) {
    setFieldErrors((prev) => {
      if (prev[key] == null) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (isDemoMode) return

    const nextErrors: AddGoalFieldErrors = {}

    if (!form.name.trim()) {
      nextErrors.name = "Give this goal a short name—you will thank yourself later."
    }

    if (form.uiKind === "RECURRING_CASH_FLOW") {
      const amt = form.recurrence_amount
      if (amt == null || Number.isNaN(amt)) {
        nextErrors.recurrence_amount = "How much is each payment (₹)?"
      } else if (amt <= 0) {
        nextErrors.recurrence_amount = "Use an amount above zero."
      } else {
        const monthly = recurrenceAmountToMonthlyInr(amt, form.recurrence_frequency)
        if (monthly < MIN_MONTHLY_GOAL_CONTRIBUTION_INR) {
          nextErrors.recurrence_amount = `That is very small once split by month—use at least ₹${MIN_MONTHLY_GOAL_CONTRIBUTION_INR.toLocaleString("en-IN")} per month in today's money, or Simulate will treat it as zero.`
        }
      }
      if (!form.recurrence_start?.trim()) {
        nextErrors.recurrence_start = "When is the first payment due?"
      }
    }

    setFieldErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) {
      return
    }

    const payload = addGoalFormToCreatePayload({
      ...form,
      name: form.name.trim(),
      notes: form.notes.trim(),
    })

    create(payload, {
      onSuccess: () => {
        setOpen(false)
        resetAddForm()
      },
    })
  }

  const kind = form.uiKind

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger
        render={
          <Button
            size="sm"
            className="h-7 gap-1 text-xs"
            disabled={isDemoMode}
            title={
              isDemoMode
                ? "Demo mode uses a fixed sample goal list — adding goals is turned off."
                : undefined
            }
          >
            <Plus className="size-3" />
            Add Goal
          </Button>
        }
      />
      <SheetContent className="flex h-full w-[360px] flex-col sm:w-[400px]">
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 pb-10 pt-5">
          <SheetHeader className="shrink-0 space-y-2 p-0 pr-12 pb-4">
            <SheetTitle>New goal</SheetTitle>
            <SheetDescription>
              Tell us what you&apos;re aiming for—we&apos;ll only show the fields that matter. Spend
              caps use your live bank data; everything else goes by the numbers you enter here.
            </SheetDescription>
          </SheetHeader>

          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor="goal-name">Name</Label>
              <Input
                id="goal-name"
                placeholder="e.g. Down payment fund"
                value={form.name}
                aria-invalid={fieldErrors.name ? true : undefined}
                onChange={(e) => {
                  setField("name", e.target.value)
                  clearFieldError("name")
                }}
              />
              {fieldErrors.name ? (
                <p className="text-xs text-destructive" role="alert">
                  {fieldErrors.name}
                </p>
              ) : null}
            </div>

            <div className="flex w-full min-w-0 flex-col gap-2">
              <Label htmlFor="add-goal-kind">This goal is</Label>
              <Select
                value={form.uiKind}
                onValueChange={(v) => {
                  const next = v as GoalUiKind
                  setForm((f) => {
                    let nextSubtype = f.goal_subtype
                    if (next === "POINT_IN_TIME") {
                      nextSubtype = f.goal_subtype || "CUSTOM"
                    } else {
                      nextSubtype = undefined
                    }
                    if (next === "RECURRING_CASH_FLOW") {
                      return {
                        ...f,
                        uiKind: next,
                        goal_subtype: nextSubtype,
                        target_amount: undefined,
                        target_date: undefined,
                      }
                    }
                    return { ...f, uiKind: next, goal_subtype: nextSubtype }
                  })
                  setFieldErrors((prev) => {
                    if (next !== "RECURRING_CASH_FLOW") {
                      return { ...prev, recurrence_amount: undefined, recurrence_start: undefined }
                    }
                    return prev
                  })
                }}
              >
                <SelectTrigger
                  id="add-goal-kind"
                  className="h-auto min-h-8 w-full min-w-0 whitespace-normal py-1.5 *:data-[slot=select-value]:line-clamp-none *:data-[slot=select-value]:text-left"
                >
                  <SelectValue placeholder="Choose one">
                    {labelGoalUiKind(form.uiKind)}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {(Object.entries(GOAL_UI_KIND_LABELS) as [GoalUiKind, string][]).map(
                    ([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ),
                  )}
                </SelectContent>
              </Select>
            </div>

            {kind === "POINT_IN_TIME" && (
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-infl-category">How prices move for this goal</Label>
                <p className="text-xs text-muted-foreground leading-snug">
                  Each line is Arth&apos;s default yearly bump for Simulate unless you change
                  &quot;Goal inflation %&quot; below. Pick the story closest to what you&apos;re
                  saving for—education and housing don&apos;t climb at the same rate as everyday
                  prices.
                </p>
                <Select
                  value={form.goal_subtype || "CUSTOM"}
                  onValueChange={(v) => setField("goal_subtype", v ?? undefined)}
                >
                  <SelectTrigger id="add-infl-category" className="w-full min-w-0">
                    <SelectValue placeholder="Pick one">
                      {inflationSelectLabelForSubtype(
                        form.goal_subtype || "CUSTOM",
                        DEFAULT_HEADLINE_INFLATION_PCT,
                      )}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {GOAL_SUBTYPE_OPTIONS_INFLATION.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {inflationSelectLabelForSubtype(o.value, DEFAULT_HEADLINE_INFLATION_PCT)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {kind !== "RECURRING_CASH_FLOW" ? (
              <div className="grid grid-cols-2 gap-4">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="goal-target">
                    {kind === "EXPENSE_LIMIT" ? "Cap (₹)" : "Target amount (₹, today)"}
                  </Label>
                  <Input
                    id="goal-target"
                    type="text"
                    inputMode="decimal"
                    autoComplete="off"
                    placeholder="e.g. 50,000"
                    className="tabular-nums"
                    value={
                      form.target_amount != null ? formatInrMoneyInput(form.target_amount) : ""
                    }
                    onChange={(e) => {
                      const n = parseInrMoneyInput(e.target.value)
                      setField("target_amount", n === null ? undefined : n)
                    }}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="goal-date">Target date (optional)</Label>
                  <Input
                    id="goal-date"
                    type="date"
                    min="1900-01-01"
                    max="9999-12-31"
                    value={form.target_date ?? ""}
                    onChange={(e) => {
                      const raw = e.target.value
                      if (raw === "") {
                        setField("target_date", undefined)
                        return
                      }
                      const v = sanitizeHtmlDateInputValue(raw)
                      if (v != null) setField("target_date", v)
                    }}
                  />
                </div>
              </div>
            ) : null}

            {kind === "POINT_IN_TIME" ? (
              <GoalTargetMoneyHint
                rawTargetInput={
                  form.target_amount != null ? formatInrMoneyInput(form.target_amount) : ""
                }
                targetDate={form.target_date ?? ""}
                goalSpecificInflationInput={
                  form.goal_specific_inflation_rate != null
                    ? String(form.goal_specific_inflation_rate)
                    : ""
                }
                inflationResolution={previewInflationResolutionForForm(
                  form.goal_subtype,
                  DEFAULT_HEADLINE_INFLATION_PCT,
                )}
              />
            ) : null}

            {kind === "POINT_IN_TIME" && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="add-sb">Already put away (₹)</Label>
                    <Input
                      id="add-sb"
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      placeholder="0"
                      className="tabular-nums"
                      value={
                        form.starting_balance != null
                          ? formatInrMoneyInput(form.starting_balance)
                          : ""
                      }
                      onChange={(e) => {
                        const n = parseInrMoneyInput(e.target.value)
                        setField("starting_balance", n === null ? undefined : n)
                      }}
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="add-er">Expected yearly return % (optional)</Label>
                    <Input
                      id="add-er"
                      type="number"
                      step="0.1"
                      placeholder="e.g. 10"
                      value={form.expected_return_rate ?? ""}
                      onChange={(e) =>
                        setField(
                          "expected_return_rate",
                          e.target.value ? parseFloat(e.target.value) : undefined,
                        )
                      }
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="add-infl">Your own inflation % (optional)</Label>
                  <Input
                    id="add-infl"
                    type="number"
                    step="0.1"
                    placeholder="e.g. 6"
                    value={form.goal_specific_inflation_rate ?? ""}
                    onChange={(e) =>
                      setField(
                        "goal_specific_inflation_rate",
                        e.target.value ? parseFloat(e.target.value) : undefined,
                      )
                    }
                  />
                </div>
              </>
            )}

            {kind === "RECURRING_CASH_FLOW" && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="add-ramt">{"Amount each time (₹, in today's prices)"}</Label>
                    <Input
                      id="add-ramt"
                      type="text"
                      inputMode="decimal"
                      autoComplete="off"
                      className="tabular-nums"
                      aria-invalid={fieldErrors.recurrence_amount ? true : undefined}
                      aria-describedby={
                        fieldErrors.recurrence_amount ? "add-ramt-error" : undefined
                      }
                      value={
                        form.recurrence_amount != null
                          ? formatInrMoneyInput(form.recurrence_amount)
                          : ""
                      }
                      onChange={(e) => {
                        const n = parseInrMoneyInput(e.target.value)
                        setField("recurrence_amount", n === null ? undefined : n)
                        clearFieldError("recurrence_amount")
                      }}
                    />
                    {fieldErrors.recurrence_amount ? (
                      <p id="add-ramt-error" className="text-xs text-destructive" role="alert">
                        {fieldErrors.recurrence_amount}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex min-w-0 flex-col gap-2">
                    <Label>Frequency</Label>
                    <Select
                      value={form.recurrence_frequency}
                      onValueChange={(v) =>
                        setField("recurrence_frequency", v as AddGoalFormState["recurrence_frequency"])
                      }
                    >
                      <SelectTrigger className="w-full min-w-0">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="MONTHLY">Every month</SelectItem>
                        <SelectItem value="QUARTERLY">Every quarter</SelectItem>
                        <SelectItem value="ANNUAL">Once a year</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="add-rs">First payment date</Label>
                    <Input
                      id="add-rs"
                      type="date"
                      min="1900-01-01"
                      max="9999-12-31"
                      aria-invalid={fieldErrors.recurrence_start ? true : undefined}
                      aria-describedby={
                        fieldErrors.recurrence_start ? "add-rs-error" : undefined
                      }
                      value={form.recurrence_start ?? ""}
                      onChange={(e) => {
                        const raw = e.target.value
                        if (raw === "") {
                          setField("recurrence_start", undefined)
                          clearFieldError("recurrence_start")
                          return
                        }
                        const v = sanitizeHtmlDateInputValue(raw)
                        if (v != null) {
                          setField("recurrence_start", v)
                          clearFieldError("recurrence_start")
                        }
                      }}
                    />
                    {fieldErrors.recurrence_start ? (
                      <p id="add-rs-error" className="text-xs text-destructive" role="alert">
                        {fieldErrors.recurrence_start}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="add-re">Last payment (optional)</Label>
                    <Input
                      id="add-re"
                      type="date"
                      min="1900-01-01"
                      max="9999-12-31"
                      value={form.recurrence_end ?? ""}
                      onChange={(e) => {
                        const raw = e.target.value
                        if (raw === "") {
                          setField("recurrence_end", undefined)
                          return
                        }
                        const v = sanitizeHtmlDateInputValue(raw)
                        if (v != null) setField("recurrence_end", v)
                      }}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <Label>Bill type (optional)</Label>
                  <Select
                    value={form.goal_subtype || "none"}
                    onValueChange={(v) =>
                      setField(
                        "goal_subtype",
                        v == null || v === "none" ? undefined : v,
                      )
                    }
                  >
                    <SelectTrigger className="w-full min-w-0">
                      <SelectValue placeholder="Optional" />
                    </SelectTrigger>
                    <SelectContent>
                      {GOAL_SUBTYPE_OPTIONS.map((o) => (
                        <SelectItem key={o.value || "none"} value={o.value || "none"}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </>
            )}

            {kind === "EXPENSE_LIMIT" && (
              <div className="flex w-full min-w-0 flex-col gap-2">
                <Label>Measure spending against</Label>
                <Select
                  value={form.progress_cadence}
                  onValueChange={(v) => setField("progress_cadence", v as "MONTHLY" | "ANNUAL")}
                >
                  <SelectTrigger className="w-full min-w-0">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="MONTHLY">This month only</SelectItem>
                    <SelectItem value="ANNUAL">Year so far (Jan to today)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="flex flex-col gap-2">
              <Label htmlFor="goal-notes">Notes (optional)</Label>
              <Textarea
                id="goal-notes"
                rows={3}
                placeholder="Anything you want future-you to remember"
                value={form.notes}
                onChange={(e) => setField("notes", e.target.value)}
              />
            </div>

            <Button type="submit" className="mt-1 w-full" disabled={isPending}>
              {isPending ? "Saving…" : "Save goal"}
            </Button>
          </form>
        </div>
      </SheetContent>
    </Sheet>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// GoalsSection — the main component
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  className?: string
  /** From <code>?chart_key=</code> when opening Goals from a dashboard chart. */
  initialChartKey?: string | null
}

export function GoalsSection({ className, initialChartKey = null }: Props) {
  const { data: goals, isLoading } = useGoals()

  const sorted = React.useMemo(() => sortGoalsForDisplay(goals ?? []), [goals])
  const inPlayGoals = sorted.filter((g) => g.activation_status !== "COMPLETED")
  const completedGoals = sorted.filter((g) => g.activation_status === "COMPLETED")

  return (
    <div className={cn("space-y-6", className)}>

      {/* ── Section header ── */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Goals</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            {goals
              ? `${goals.length} goal${goals.length !== 1 ? "s" : ""} · tracking your financial targets`
              : "Your financial targets"}
          </p>
        </div>
        <AddGoalSheet prefillChartKey={initialChartKey} />
      </div>

      {/* ── Content ── */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {[...Array(3)].map((_, i) => (
            <Skeleton key={i} className="h-56" />
          ))}
        </div>
      ) : (goals ?? []).length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center gap-4 rounded-xl border border-dashed">
          <div className="size-16 rounded-2xl bg-muted flex items-center justify-center">
            <Target className="size-8 opacity-40" />
          </div>
          <div>
            <p className="font-semibold">No goals yet</p>
            <p className="text-sm text-muted-foreground mt-1 max-w-xs mx-auto">
              Set your first goal — retirement, home purchase, emergency fund — and we&apos;ll track your progress here.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-8">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {inPlayGoals.map((goal) => (
              <GoalCard key={goal.id} goal={goal} />
            ))}
          </div>
          {completedGoals.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-4">
                <CheckCircle2 className="size-4 text-muted-foreground" />
                <p className="text-sm font-medium text-muted-foreground">Completed</p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 opacity-60">
                {completedGoals.map((goal) => (
                  <GoalCard key={goal.id} goal={goal} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
