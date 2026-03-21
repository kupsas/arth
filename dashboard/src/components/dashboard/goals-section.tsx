/**
 * GoalsSection — displays all financial goals with live progress bars.
 *
 * Phase 4.5d: Goals Table + API
 *
 * Shows each goal with:
 *   - Name + goal type badge
 *   - Progress bar (current value / target amount)
 *   - Status badge (ON_TRACK / AT_RISK / BEHIND / ACHIEVED / PAUSED)
 *   - Days until deadline (if set)
 *
 * Also includes a "+ Add Goal" sheet for creating goals and a pencil "Edit" sheet per row.
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
import { useCreateGoal, useDeleteGoal, useGoals, useUpdateGoal } from "@/hooks/use-goals"
import {
  CHART_KEY_EXPENSE_NEED_WANT_STACK,
  CHART_KEY_INVESTMENT_NET,
  categoryChartKey,
} from "@/lib/chart-keys"
import { formatCurrency, cn } from "@/lib/utils"
import type {
  DashboardCategorySeries,
  Goal,
  GoalCreate,
  GoalStatus,
  GoalType,
  GoalUpdate,
  ProgressCadence,
} from "@/lib/types"

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

const GOAL_TYPE_LABELS: Record<GoalType, string> = {
  SAVINGS:        "Savings",
  EXPENSE_LIMIT:  "Expense Limit",
  EMERGENCY_FUND: "Emergency Fund",
  INVESTMENT:     "Investment",
  DEBT_PAYOFF:    "Debt Payoff",
  INSURANCE:      "Insurance",
  TAX:            "Tax",
}

/** Category mini-charts on the dashboard — labels match CategoryTrendGrid. */
const DASHBOARD_CATEGORY_SERIES: { id: DashboardCategorySeries; label: string }[] = [
  { id: "swiggy_instamart", label: "Swiggy Instamart" },
  { id: "swiggy_food", label: "Swiggy Food" },
  { id: "food_and_dining", label: "Food & dining + Swiggy Dineout" },
  { id: "shopping", label: "Shopping & e‑commerce" },
  { id: "transport", label: "Transport & fuel" },
  { id: "travel", label: "Travel & stay" },
  { id: "gifts", label: "Gifts & personal transfers" },
]

// ─────────────────────────────────────────────────────────────────────────────
// EditGoalSheet — change name, targets, notes (goal type is fixed after create)
// ─────────────────────────────────────────────────────────────────────────────

function EditGoalSheet({ goal }: { goal: Goal }) {
  const [open, setOpen] = React.useState(false)
  const { mutate: patchGoal, isPending } = useUpdateGoal()

  // Form state — re-seed whenever the sheet opens so you always edit fresh server data.
  const [name, setName] = React.useState(goal.name)
  const [targetAmount, setTargetAmount] = React.useState(
    goal.target_amount != null ? String(goal.target_amount) : "",
  )
  const [targetDate, setTargetDate] = React.useState(goal.target_date ?? "")
  const [linkedCategory, setLinkedCategory] = React.useState(goal.linked_category ?? "")
  /** Non-empty = bind to dashboard chart_key; empty = use linked category name (legacy). */
  const [expenseChartBind, setExpenseChartBind] = React.useState(goal.chart_key ?? "")
  const [progressCadence, setProgressCadence] = React.useState<ProgressCadence>(
    goal.progress_cadence ?? "MONTHLY",
  )
  const [notes, setNotes] = React.useState(goal.notes ?? "")

  React.useEffect(() => {
    if (!open) return
    setName(goal.name)
    setTargetAmount(goal.target_amount != null ? String(goal.target_amount) : "")
    setTargetDate(goal.target_date ?? "")
    setLinkedCategory(goal.linked_category ?? "")
    setExpenseChartBind(goal.chart_key ?? "")
    setProgressCadence(goal.progress_cadence ?? "MONTHLY")
    setNotes(goal.notes ?? "")
  }, [open, goal])

  const isExpenseLimit = goal.goal_type === "EXPENSE_LIMIT"

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return

    const update: GoalUpdate = {
      name: name.trim(),
      target_date: targetDate.trim() ? targetDate.trim() : null,
      notes: notes.trim() ? notes.trim() : null,
    }

    // Empty target field → clear limit/target on the server
    if (targetAmount.trim() === "") {
      update.target_amount = null
    } else {
      const n = parseFloat(targetAmount)
      if (!Number.isNaN(n)) update.target_amount = n
    }

    if (isExpenseLimit) {
      if (expenseChartBind.trim()) {
        update.chart_key = expenseChartBind.trim()
        update.linked_category = null
      } else {
        update.chart_key = null
        update.linked_category = linkedCategory.trim() ? linkedCategory.trim() : null
      }
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
        {/* Padded scroll body: SheetHeader + form were edge-to-edge; inputs need inset from sheet chrome */}
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 pb-10 pt-5">
          <SheetHeader className="shrink-0 space-y-2 p-0 pr-12 pb-4">
            <SheetTitle>Edit goal</SheetTitle>
            <SheetDescription>
              Update this goal&apos;s details. Type is{" "}
              <span className="font-medium text-foreground">
                {GOAL_TYPE_LABELS[goal.goal_type as GoalType] ?? goal.goal_type}
              </span>{" "}
              (create a new goal if you need a different type).
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
                {isExpenseLimit
                  ? progressCadence === "ANNUAL"
                    ? "Annual cap (₹)"
                    : "Monthly cap (₹)"
                  : "Target amount (₹)"}
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
              <Label htmlFor={`edit-goal-date-${goal.id}`}>Deadline (optional)</Label>
              <Input
                id={`edit-goal-date-${goal.id}`}
                type="date"
                value={targetDate}
                onChange={(e) => setTargetDate(e.target.value)}
              />
            </div>
          </div>

          {isExpenseLimit && (
            <>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-goal-chart-${goal.id}`}>Dashboard chart (recommended)</Label>
                <Select
                  value={expenseChartBind || "__legacy__"}
                  onValueChange={(v) =>
                    setExpenseChartBind(!v || v === "__legacy__" ? "" : v)
                  }
                >
                  <SelectTrigger id={`edit-goal-chart-${goal.id}`}>
                    <SelectValue placeholder="Choose chart…" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={CHART_KEY_EXPENSE_NEED_WANT_STACK}>
                      Expense chart — total NEED+WANT
                    </SelectItem>
                    {DASHBOARD_CATEGORY_SERIES.map((s) => (
                      <SelectItem key={s.id} value={categoryChartKey(s.id)}>
                        Chart — {s.label}
                      </SelectItem>
                    ))}
                    <SelectItem value="__legacy__">Custom — category name only (legacy)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-goal-cadence-${goal.id}`}>Progress period</Label>
                <Select
                  value={progressCadence}
                  onValueChange={(v) => setProgressCadence(v as ProgressCadence)}
                >
                  <SelectTrigger id={`edit-goal-cadence-${goal.id}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="MONTHLY">
                      Monthly (shows on dashboard &quot;This month so far&quot;)
                    </SelectItem>
                    <SelectItem value="ANNUAL">
                      Annual (Jan 1 — today vs cap; dashboard headline only lists monthly goals)
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {!expenseChartBind && (
                <div className="flex flex-col gap-2">
                  <Label htmlFor={`edit-goal-category-${goal.id}`}>
                    Counterparty category (legacy)
                  </Label>
                  <Input
                    id={`edit-goal-category-${goal.id}`}
                    placeholder="e.g. Food & Dining"
                    value={linkedCategory}
                    onChange={(e) => setLinkedCategory(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    Matches ``counterparty_category`` only — not the same as combined dashboard
                    charts (e.g. Food + Dineout).
                  </p>
                </div>
              )}
            </>
          )}
          {goal.goal_type === "INVESTMENT" && (
            <p className="text-xs text-muted-foreground">
              Linked to dashboard chart <span className="font-mono text-foreground">{CHART_KEY_INVESTMENT_NET}</span>{" "}
              (monthly net investment).
            </p>
          )}

          <div className="flex flex-col gap-2">
            <Label htmlFor={`edit-goal-notes-${goal.id}`}>Notes (optional)</Label>
            <Textarea
              id={`edit-goal-notes-${goal.id}`}
              rows={2}
              placeholder="Why this goal matters…"
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

  const isExpenseLimit = goal.goal_type === "EXPENSE_LIMIT"
  // For expense limits: invert the progress bar (higher spend = closer to limit)
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
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Target className="size-3.5 shrink-0 text-muted-foreground mt-0.5" />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{goal.name}</p>
            <p className="text-xs text-muted-foreground">
              {GOAL_TYPE_LABELS[goal.goal_type as GoalType] ?? goal.goal_type}
              {goal.chart_key && ` · ${goal.chart_key}`}
              {!goal.chart_key && goal.linked_category && ` · ${goal.linked_category}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {goal.goal_type === "EXPENSE_LIMIT" &&
            (goal.progress_cadence ?? "MONTHLY") === "ANNUAL" && (
              <Badge variant="secondary" className="text-[10px] px-1 py-0">
                Annual
              </Badge>
            )}
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

      {/* Progress bar */}
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
                {goal.target_amount ? (
                  <>
                    {" "}
                    of {formatCurrency(goal.target_amount)}
                    {(goal.progress_cadence ?? "MONTHLY") === "ANNUAL"
                      ? " annual cap (YTD)"
                      : " monthly limit"}
                  </>
                ) : (
                  ""
                )}
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

      {/* Manual value edit (for non-auto-computed goals) */}
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

// ─────────────────────────────────────────────────────────────────────────────
// AddGoalSheet — slide-in form for creating a new goal
// ─────────────────────────────────────────────────────────────────────────────

const defaultAddForm = (): Partial<GoalCreate> => ({
  goal_type: "EXPENSE_LIMIT",
  priority: 3,
  linked_layer: 3,
  user_id: "sashank",
})

function AddGoalSheet({ prefillChartKey }: { prefillChartKey?: string | null }) {
  const [open, setOpen] = React.useState(false)
  const { mutate: create, isPending } = useCreateGoal()

  const [form, setForm] = React.useState<Partial<GoalCreate>>(defaultAddForm())
  /** When set, create/update EXPENSE_LIMIT with this chart_key (else use linked_category). */
  const [expenseChartBind, setExpenseChartBind] = React.useState("")
  const [expenseCadence, setExpenseCadence] = React.useState<ProgressCadence>("MONTHLY")

  function resetAddForm() {
    setForm(defaultAddForm())
    setExpenseChartBind("")
    setExpenseCadence("MONTHLY")
  }

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (next && prefillChartKey) {
      if (prefillChartKey === CHART_KEY_INVESTMENT_NET) {
        setForm({ ...defaultAddForm(), goal_type: "INVESTMENT" })
        setExpenseChartBind("")
      } else {
        setForm({ ...defaultAddForm(), goal_type: "EXPENSE_LIMIT" })
        setExpenseChartBind(prefillChartKey)
      }
    }
    if (!next) resetAddForm()
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name || !form.goal_type) return

    const base: GoalCreate = {
      name: form.name.trim(),
      goal_type: form.goal_type,
      target_amount: form.target_amount,
      target_date: form.target_date,
      priority: form.priority ?? 3,
      linked_layer: form.linked_layer ?? 3,
      user_id: form.user_id ?? "sashank",
      notes: form.notes,
    }

    if (form.goal_type === "EXPENSE_LIMIT") {
      base.progress_cadence = expenseCadence
      if (expenseChartBind.trim()) {
        base.chart_key = expenseChartBind.trim()
      } else {
        base.linked_category = form.linked_category?.trim()
          ? form.linked_category.trim()
          : undefined
      }
    }
    if (form.goal_type === "INVESTMENT") {
      base.chart_key = CHART_KEY_INVESTMENT_NET
    }

    create(base, {
      onSuccess: () => {
        setOpen(false)
        resetAddForm()
      },
    })
  }

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
            <SheetTitle>New Goal</SheetTitle>
            <SheetDescription>
              Set a financial target to track. Progress is computed automatically
              for Expense Limit goals; enter it manually for everything else.
            </SheetDescription>
          </SheetHeader>

          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor="goal-name">Goal name</Label>
            <Input
              id="goal-name"
              placeholder="e.g. Keep food spending under ₹8k"
              value={form.name ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              required
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label>Goal type</Label>
            <Select
              value={form.goal_type}
              onValueChange={(v) => setForm((f) => ({ ...f, goal_type: v as GoalType }))}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(GOAL_TYPE_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="goal-target">
                {form.goal_type === "EXPENSE_LIMIT"
                  ? expenseCadence === "ANNUAL"
                    ? "Annual cap (₹)"
                    : "Monthly cap (₹)"
                  : "Target amount (₹)"}
              </Label>
              <Input
                id="goal-target"
                type="number"
                placeholder="e.g. 10000"
                value={form.target_amount ?? ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    target_amount: e.target.value ? parseFloat(e.target.value) : undefined,
                  }))
                }
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="goal-date">Deadline (optional)</Label>
              <Input
                id="goal-date"
                type="date"
                value={form.target_date ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, target_date: e.target.value || undefined }))}
              />
            </div>
          </div>

          {form.goal_type === "EXPENSE_LIMIT" && (
            <>
              <div className="flex flex-col gap-2">
                <Label htmlFor="goal-chart-bind">Cap matches dashboard chart</Label>
                <Select
                  value={expenseChartBind || "__legacy__"}
                  onValueChange={(v) =>
                    setExpenseChartBind(!v || v === "__legacy__" ? "" : v)
                  }
                >
                  <SelectTrigger id="goal-chart-bind">
                    <SelectValue placeholder="Choose…" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={CHART_KEY_EXPENSE_NEED_WANT_STACK}>
                      Expense chart — total NEED+WANT per month
                    </SelectItem>
                    {DASHBOARD_CATEGORY_SERIES.map((s) => (
                      <SelectItem key={s.id} value={categoryChartKey(s.id)}>
                        Category chart — {s.label}
                      </SelectItem>
                    ))}
                    <SelectItem value="__legacy__">
                      Custom — single counterparty category (legacy)
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="goal-cadence-add">Progress period</Label>
                <Select
                  value={expenseCadence}
                  onValueChange={(v) => setExpenseCadence(v as ProgressCadence)}
                >
                  <SelectTrigger id="goal-cadence-add">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="MONTHLY">Monthly (dashboard headline + charts)</SelectItem>
                    <SelectItem value="ANNUAL">Annual (YTD vs cap; not on dashboard headline)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {!expenseChartBind && (
                <div className="flex flex-col gap-2">
                  <Label htmlFor="goal-category">Counterparty category name</Label>
                  <Input
                    id="goal-category"
                    placeholder="e.g. Food & Dining"
                    value={form.linked_category ?? ""}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, linked_category: e.target.value || undefined }))
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    Empty + legacy mode defaults to the same total as the expense chart (NEED+WANT).
                  </p>
                </div>
              )}
            </>
          )}
          {form.goal_type === "INVESTMENT" && (
            <p className="text-xs text-muted-foreground">
              Progress uses monthly <strong>net</strong> investment (purchases − sales), same as the
              Investments chart.
            </p>
          )}

          <div className="flex flex-col gap-2">
            <Label htmlFor="goal-notes">Notes (optional)</Label>
            <Textarea
              id="goal-notes"
              rows={2}
              placeholder="Why this goal matters…"
              value={form.notes ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value || undefined }))}
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
              {goals ? `${goals.length} goal${goals.length !== 1 ? "s" : ""}` : "Track your financial targets"}
            </p>
          </div>
          <AddGoalSheet prefillChartKey={initialChartKey} />
        </div>
      </CardHeader>

      <CardContent className="space-y-2">
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-20" />)}
          </div>
        ) : (goals ?? []).length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center text-sm text-muted-foreground gap-2">
            <Target className="size-8 opacity-30" />
            <p>No goals yet.</p>
            <p className="text-xs">Add your first goal to start tracking progress.</p>
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
