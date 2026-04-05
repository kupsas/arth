/**
 * GoalsSection — compact list of financial goals (name, goal “kind”, target, progress, notes).
 *
 * The sheet uses `goal_class`-style choices (one-time, recurring, growth, spending cap);
 * the client maps each choice to API `goal_type` + `goal_class` on create/update.
 */

"use client"

import * as React from "react"
import {
  CheckCircle2,
  Clock,
  Pencil,
  Plus,
  Target,
  Trash2,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
import { formatCurrency, cn } from "@/lib/utils"
import type { Goal, GoalStatus, GoalUpdate } from "@/lib/types"

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

// ─────────────────────────────────────────────────────────────────────────────
// Status helpers
// ─────────────────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<GoalStatus, string> = {
  ON_TRACK: "border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-400",
  AT_RISK:  "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  BEHIND:   "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
  ACHIEVED: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
  PAUSED:   "border-gray-500/30 bg-gray-500/10 text-gray-600 dark:text-gray-400",
}

const STATUS_LABELS: Record<GoalStatus, string> = {
  ON_TRACK: "On Track",
  AT_RISK:  "At Risk",
  BEHIND:   "Behind",
  ACHIEVED: "Achieved",
  PAUSED:   "Paused",
}

// ─────────────────────────────────────────────────────────────────────────────
// EditGoalSheet — fields depend on inferred goal “kind” (goal_class + legacy goal_type)
// ─────────────────────────────────────────────────────────────────────────────

function EditGoalSheet({ goal }: { goal: Goal }) {
  const [open, setOpen] = React.useState(false)
  const { mutate: patchGoal, isPending } = useUpdateGoal()
  const uiKind = inferGoalUiKind(goal)

  const [name, setName] = React.useState(goal.name)
  const [targetAmount, setTargetAmount] = React.useState(
    goal.target_amount != null ? String(goal.target_amount) : "",
  )
  const [targetDate, setTargetDate] = React.useState(goal.target_date ?? "")
  const [notes, setNotes] = React.useState(goal.notes ?? "")
  const [startingBalance, setStartingBalance] = React.useState(
    goal.starting_balance != null ? String(goal.starting_balance) : "",
  )
  const [goalInflation, setGoalInflation] = React.useState(
    goal.goal_specific_inflation_rate != null ? String(goal.goal_specific_inflation_rate) : "",
  )
  const [expectedReturn, setExpectedReturn] = React.useState(
    goal.expected_return_rate != null ? String(goal.expected_return_rate) : "",
  )
  const [recurrenceAmount, setRecurrenceAmount] = React.useState(
    goal.recurrence_amount != null ? String(goal.recurrence_amount) : "",
  )
  const [recurrenceFrequency, setRecurrenceFrequency] = React.useState(
    (goal.recurrence_frequency as AddGoalFormState["recurrence_frequency"]) ?? "MONTHLY",
  )
  const [recurrenceStart, setRecurrenceStart] = React.useState(goal.recurrence_start ?? "")
  const [recurrenceEnd, setRecurrenceEnd] = React.useState(goal.recurrence_end ?? "")
  const [goalSubtype, setGoalSubtype] = React.useState(goal.goal_subtype ?? "")
  const [progressCadence, setProgressCadence] = React.useState(
    (goal.progress_cadence as "MONTHLY" | "ANNUAL") ?? "MONTHLY",
  )

  React.useEffect(() => {
    if (!open) return
    setName(goal.name)
    setTargetAmount(goal.target_amount != null ? String(goal.target_amount) : "")
    setTargetDate(goal.target_date ?? "")
    setNotes(goal.notes ?? "")
    setStartingBalance(goal.starting_balance != null ? String(goal.starting_balance) : "")
    setGoalInflation(
      goal.goal_specific_inflation_rate != null ? String(goal.goal_specific_inflation_rate) : "",
    )
    setExpectedReturn(goal.expected_return_rate != null ? String(goal.expected_return_rate) : "")
    setRecurrenceAmount(goal.recurrence_amount != null ? String(goal.recurrence_amount) : "")
    setRecurrenceFrequency(
      (goal.recurrence_frequency as AddGoalFormState["recurrence_frequency"]) ?? "MONTHLY",
    )
    setRecurrenceStart(goal.recurrence_start ?? "")
    setRecurrenceEnd(goal.recurrence_end ?? "")
    setGoalSubtype(goal.goal_subtype ?? "")
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
    if (!name.trim()) return

    const update: GoalUpdate = {
      name: name.trim(),
      target_date: targetDate.trim() ? targetDate.trim() : null,
      notes: notes.trim() ? notes.trim() : null,
    }

    if (targetAmount.trim() === "") {
      update.target_amount = null
    } else {
      const n = parseFloat(targetAmount)
      if (!Number.isNaN(n)) update.target_amount = n
    }

    if (uiKind === "POINT_IN_TIME") {
      const sb = parseOptFloat(startingBalance)
      if (sb !== undefined) update.starting_balance = sb
      const inf = parseOptFloat(goalInflation)
      if (inf !== undefined) update.goal_specific_inflation_rate = inf
    }
    if (uiKind === "GROWTH") {
      const sb = parseOptFloat(startingBalance)
      if (sb !== undefined) {
        update.starting_balance = sb
        update.current_value = sb
      }
      const inf = parseOptFloat(goalInflation)
      if (inf !== undefined) update.goal_specific_inflation_rate = inf
      const er = parseOptFloat(expectedReturn)
      if (er !== undefined) update.expected_return_rate = er
    }
    if (uiKind === "RECURRING_CASH_FLOW") {
      const ra = parseOptFloat(recurrenceAmount)
      if (ra !== undefined) update.recurrence_amount = ra
      update.recurrence_frequency = recurrenceFrequency
      update.recurrence_start = recurrenceStart.trim() ? recurrenceStart.trim() : null
      update.recurrence_end = recurrenceEnd.trim() ? recurrenceEnd.trim() : null
      update.goal_subtype = goalSubtype.trim() ? goalSubtype.trim() : null
    }
    if (uiKind === "EXPENSE_LIMIT") {
      update.progress_cadence = progressCadence
    }

    patchGoal(
      { id: goal.id, update },
      { onSuccess: () => setOpen(false) },
    )
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
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
              Kind:{" "}
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

            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-goal-target-${goal.id}`}>
                  {isExpenseLimit ? "Cap / limit (₹)" : "Target amount (₹)"}
                </Label>
                <Input
                  id={`edit-goal-target-${goal.id}`}
                  type="number"
                  placeholder="e.g. 10000"
                  value={targetAmount}
                  onChange={(e) => setTargetAmount(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-goal-date-${goal.id}`}>
                  {uiKind === "RECURRING_CASH_FLOW" ? "End-by date (optional)" : "Deadline (optional)"}
                </Label>
                <Input
                  id={`edit-goal-date-${goal.id}`}
                  type="date"
                  value={targetDate}
                  onChange={(e) => setTargetDate(e.target.value)}
                />
              </div>
            </div>

            {uiKind === "POINT_IN_TIME" && (
              <div className="grid grid-cols-2 gap-4">
                <div className="flex flex-col gap-2">
                  <Label htmlFor={`edit-start-${goal.id}`}>Already saved (₹)</Label>
                  <Input
                    id={`edit-start-${goal.id}`}
                    type="number"
                    placeholder="0"
                    value={startingBalance}
                    onChange={(e) => setStartingBalance(e.target.value)}
                  />
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
              </div>
            )}

            {uiKind === "GROWTH" && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={`edit-sb-${goal.id}`}>Current corpus (₹)</Label>
                    <Input
                      id={`edit-sb-${goal.id}`}
                      type="number"
                      placeholder="0"
                      value={startingBalance}
                      onChange={(e) => setStartingBalance(e.target.value)}
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
                  <Label htmlFor={`edit-ginfl-${goal.id}`}>Goal inflation % (optional)</Label>
                  <Input
                    id={`edit-ginfl-${goal.id}`}
                    type="number"
                    step="0.1"
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
                    <Label htmlFor={`edit-ramt-${goal.id}`}>Amount per period (₹)</Label>
                    <Input
                      id={`edit-ramt-${goal.id}`}
                      type="number"
                      required
                      value={recurrenceAmount}
                      onChange={(e) => setRecurrenceAmount(e.target.value)}
                    />
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
                      required
                      value={recurrenceStart}
                      onChange={(e) => setRecurrenceStart(e.target.value)}
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor={`edit-re-${goal.id}`}>Last payment (optional)</Label>
                    <Input
                      id={`edit-re-${goal.id}`}
                      type="date"
                      value={recurrenceEnd}
                      onChange={(e) => setRecurrenceEnd(e.target.value)}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <Label>Subtype (optional)</Label>
                  <Select
                    value={goalSubtype || "none"}
                    onValueChange={(v) =>
                      setGoalSubtype(v == null || v === "none" ? "" : v)
                    }
                  >
                    <SelectTrigger>
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

            <Button type="submit" className="mt-1 w-full" disabled={isPending}>
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
  const [newValue, setNewValue] = React.useState(String(goal.current_value ?? ""))

  const uiKind = inferGoalUiKind(goal)
  const isExpenseLimit = uiKind === "EXPENSE_LIMIT"
  const progressValue = Math.min(goal.computed_percentage, 100)

  const daysLeft = goal.target_date
    ? Math.max(0, Math.ceil((new Date(goal.target_date).getTime() - Date.now()) / 86_400_000))
    : null

  function handleValueSave() {
    const val = parseFloat(newValue)
    if (!isNaN(val)) {
      updateGoal({ id: goal.id, update: { current_value: val } })
    }
    setEditingValue(false)
  }

  return (
    <div className="rounded-lg border bg-card px-4 py-3 space-y-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Target className="size-3.5 shrink-0 text-muted-foreground mt-0.5" />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{goal.name}</p>
            <p className="text-xs text-muted-foreground">
              {labelGoalUiKind(uiKind)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
          <Badge
            variant="outline"
            className={cn("text-[11px] px-1.5 py-0", STATUS_STYLES[goal.status as GoalStatus])}
          >
            {STATUS_LABELS[goal.status as GoalStatus] ?? goal.status}
          </Badge>
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

      <div className="space-y-1">
        <Progress
          value={progressValue}
          className={cn(
            "h-1.5",
            goal.status === "BEHIND" && "[&>div]:bg-red-500",
            goal.status === "AT_RISK" && "[&>div]:bg-amber-500",
            goal.status === "ACHIEVED" && "[&>div]:bg-blue-500",
          )}
        />
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {isExpenseLimit ? (
              <>
                {formatCurrency(goal.computed_current_value)} spent
                {goal.target_amount ? <> of {formatCurrency(goal.target_amount)}</> : ""}
              </>
            ) : (
              <>
                {formatCurrency(goal.computed_current_value)}
                {goal.target_amount ? ` of ${formatCurrency(goal.target_amount)}` : ""}
              </>
            )}
          </span>
          <div className="flex items-center gap-1.5">
            {daysLeft !== null && (
              <span className="flex items-center gap-1">
                <Clock className="size-2.5" />
                {daysLeft === 0 ? "Today" : `${daysLeft}d left`}
              </span>
            )}
            {goal.status === "ACHIEVED" && (
              <CheckCircle2 className="size-3 text-blue-500" />
            )}
          </div>
        </div>
      </div>

      {goal.notes && (
        <p className="text-xs text-muted-foreground border-t border-border/60 pt-2 whitespace-pre-wrap">
          {goal.notes}
        </p>
      )}

      {!isExpenseLimit && (
        <div className="flex items-center gap-2">
          {editingValue ? (
            <>
              <Input
                type="number"
                value={newValue}
                onChange={(e) => setNewValue(e.target.value)}
                className="h-7 text-xs w-28"
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
              onClick={() => setEditingValue(true)}
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
    setOpen(next)
    if (next) {
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

    const nextErrors: AddGoalFieldErrors = {}

    if (!form.name.trim()) {
      nextErrors.name = "Enter a name for this goal."
    }

    if (form.uiKind === "RECURRING_CASH_FLOW") {
      const amt = form.recurrence_amount
      if (amt == null || Number.isNaN(amt)) {
        nextErrors.recurrence_amount = "Enter how much each payment is (₹)."
      } else if (amt <= 0) {
        nextErrors.recurrence_amount = "Use an amount greater than zero."
      }
      if (!form.recurrence_start?.trim()) {
        nextErrors.recurrence_start = "Pick the date of the first payment."
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
          <Button size="sm" className="h-7 gap-1 text-xs">
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
              Pick what kind of goal it is, then fill the fields that apply. Spending caps
              use live transactions; everything else uses manual progress unless noted.
            </SheetDescription>
          </SheetHeader>

          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor="goal-name">Goal name</Label>
              <Input
                id="goal-name"
                placeholder="e.g. Emergency fund"
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

            <div className="flex flex-col gap-2">
              <Label>Goal kind</Label>
              <Select
                value={form.uiKind}
                onValueChange={(v) => {
                  const next = v as GoalUiKind
                  setField("uiKind", next)
                  setFieldErrors((prev) => {
                    if (next !== "RECURRING_CASH_FLOW") {
                      return { ...prev, recurrence_amount: undefined, recurrence_start: undefined }
                    }
                    return prev
                  })
                }}
              >
                <SelectTrigger>
                  <SelectValue />
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

            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <Label htmlFor="goal-target">
                  {kind === "EXPENSE_LIMIT" ? "Cap / limit (₹)" : "Target amount (₹)"}
                </Label>
                <Input
                  id="goal-target"
                  type="number"
                  placeholder="e.g. 10000"
                  value={form.target_amount ?? ""}
                  onChange={(e) =>
                    setField(
                      "target_amount",
                      e.target.value ? parseFloat(e.target.value) : undefined,
                    )
                  }
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="goal-date">
                  {kind === "RECURRING_CASH_FLOW"
                    ? "End-by (optional)"
                    : "Deadline (optional)"}
                </Label>
                <Input
                  id="goal-date"
                  type="date"
                  value={form.target_date ?? ""}
                  onChange={(e) => setField("target_date", e.target.value || undefined)}
                />
              </div>
            </div>

            {kind === "POINT_IN_TIME" && (
              <div className="grid grid-cols-2 gap-4">
                <div className="flex flex-col gap-2">
                  <Label htmlFor="add-sb">Already saved (₹)</Label>
                  <Input
                    id="add-sb"
                    type="number"
                    placeholder="0"
                    value={form.starting_balance ?? ""}
                    onChange={(e) =>
                      setField(
                        "starting_balance",
                        e.target.value ? parseFloat(e.target.value) : undefined,
                      )
                    }
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <Label htmlFor="add-infl">Goal inflation % (optional)</Label>
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
              </div>
            )}

            {kind === "GROWTH" && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="add-corpus">Current corpus (₹)</Label>
                    <Input
                      id="add-corpus"
                      type="number"
                      placeholder="0"
                      value={form.starting_balance ?? ""}
                      onChange={(e) =>
                        setField(
                          "starting_balance",
                          e.target.value ? parseFloat(e.target.value) : undefined,
                        )
                      }
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="add-er">Expected return % (optional)</Label>
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
                  <Label htmlFor="add-ginfl">Goal inflation % (optional)</Label>
                  <Input
                    id="add-ginfl"
                    type="number"
                    step="0.1"
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
                    <Label htmlFor="add-ramt">Amount per period (₹)</Label>
                    <Input
                      id="add-ramt"
                      type="number"
                      min={0.01}
                      step="any"
                      aria-invalid={fieldErrors.recurrence_amount ? true : undefined}
                      aria-describedby={
                        fieldErrors.recurrence_amount ? "add-ramt-error" : undefined
                      }
                      value={form.recurrence_amount ?? ""}
                      onChange={(e) => {
                        setField(
                          "recurrence_amount",
                          e.target.value ? parseFloat(e.target.value) : undefined,
                        )
                        clearFieldError("recurrence_amount")
                      }}
                    />
                    {fieldErrors.recurrence_amount ? (
                      <p id="add-ramt-error" className="text-xs text-destructive" role="alert">
                        {fieldErrors.recurrence_amount}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label>Frequency</Label>
                    <Select
                      value={form.recurrence_frequency}
                      onValueChange={(v) =>
                        setField("recurrence_frequency", v as AddGoalFormState["recurrence_frequency"])
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
                    <Label htmlFor="add-rs">First payment (start)</Label>
                    <Input
                      id="add-rs"
                      type="date"
                      aria-invalid={fieldErrors.recurrence_start ? true : undefined}
                      aria-describedby={
                        fieldErrors.recurrence_start ? "add-rs-error" : undefined
                      }
                      value={form.recurrence_start ?? ""}
                      onChange={(e) => {
                        setField("recurrence_start", e.target.value || undefined)
                        clearFieldError("recurrence_start")
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
                      value={form.recurrence_end ?? ""}
                      onChange={(e) => setField("recurrence_end", e.target.value || undefined)}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <Label>Subtype (optional)</Label>
                  <Select
                    value={form.goal_subtype || "none"}
                    onValueChange={(v) =>
                      setField(
                        "goal_subtype",
                        v == null || v === "none" ? undefined : v,
                      )
                    }
                  >
                    <SelectTrigger>
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
              <div className="flex flex-col gap-2">
                <Label>Progress window</Label>
                <Select
                  value={form.progress_cadence}
                  onValueChange={(v) => setField("progress_cadence", v as "MONTHLY" | "ANNUAL")}
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
              <Label htmlFor="goal-notes">Notes (optional)</Label>
              <Textarea
                id="goal-notes"
                rows={3}
                placeholder="Context, reminders…"
                value={form.notes}
                onChange={(e) => setField("notes", e.target.value)}
              />
            </div>

            <Button type="submit" className="mt-1 w-full" disabled={isPending}>
              {isPending ? "Creating…" : "Create goal"}
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

  const activeGoals = (goals ?? []).filter((g) => g.status !== "ACHIEVED" && g.status !== "PAUSED")
  const achievedGoals = (goals ?? []).filter((g) => g.status === "ACHIEVED")

  return (
    <Card className={cn(className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm font-medium">Goals</CardTitle>
            <p className="text-xs text-muted-foreground">
              {goals ? `${goals.length} goal${goals.length !== 1 ? "s" : ""}` : "Targets and notes"}
            </p>
          </div>
          <AddGoalSheet prefillChartKey={initialChartKey} />
        </div>
      </CardHeader>

      <CardContent className="space-y-2">
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-20" />
            ))}
          </div>
        ) : (goals ?? []).length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center text-sm text-muted-foreground gap-2">
            <Target className="size-8 opacity-30" />
            <p>No goals yet.</p>
            <p className="text-xs">Add a goal to get started.</p>
          </div>
        ) : (
          <>
            {activeGoals.map((goal) => (
              <GoalCard key={goal.id} goal={goal} />
            ))}
            {achievedGoals.length > 0 && (
              <div className="pt-2">
                <p className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1">
                  <CheckCircle2 className="size-3 text-blue-500" />
                  Achieved
                </p>
                {achievedGoals.map((goal) => (
                  <GoalCard key={goal.id} goal={goal} />
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
