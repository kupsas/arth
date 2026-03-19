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
 * Also includes a "+ Add Goal" sheet form for creating new goals.
 */

"use client"

import * as React from "react"
import { format } from "date-fns"
import {
  CheckCircle2,
  Clock,
  Plus,
  Target,
  Trash2,
  TrendingDown,
  TrendingUp,
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
import { formatCurrency, cn } from "@/lib/utils"
import type { Goal, GoalCreate, GoalStatus, GoalType } from "@/lib/types"

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
              {goal.linked_category && ` · ${goal.linked_category}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Badge
            variant="outline"
            className={cn("text-[11px] px-1.5 py-0", STATUS_STYLES[goal.status as GoalStatus])}
          >
            {STATUS_LABELS[goal.status as GoalStatus] ?? goal.status}
          </Badge>
          <Button
            variant="ghost"
            size="icon"
            className="size-6 text-muted-foreground hover:text-destructive"
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
                {goal.target_amount ? ` of ${formatCurrency(goal.target_amount)} limit` : ""}
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

function AddGoalSheet() {
  const [open, setOpen] = React.useState(false)
  const { mutate: create, isPending } = useCreateGoal()

  const [form, setForm] = React.useState<Partial<GoalCreate>>({
    goal_type: "EXPENSE_LIMIT",
    priority: 3,
    linked_layer: 3,
    user_id: "sashank",
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name || !form.goal_type) return
    create(form as GoalCreate, {
      onSuccess: () => {
        setOpen(false)
        setForm({ goal_type: "EXPENSE_LIMIT", priority: 3, linked_layer: 3, user_id: "sashank" })
      },
    })
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={
          <Button size="sm" className="h-7 gap-1 text-xs">
            <Plus className="size-3" />
            Add Goal
          </Button>
        }
      />
      <SheetContent className="w-[360px] sm:w-[400px]">
        <SheetHeader>
          <SheetTitle>New Goal</SheetTitle>
          <SheetDescription>
            Set a financial target to track. Progress is computed automatically
            for Expense Limit goals; enter it manually for everything else.
          </SheetDescription>
        </SheetHeader>

        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="goal-name">Goal name</Label>
            <Input
              id="goal-name"
              placeholder="e.g. Keep food spending under ₹8k"
              value={form.name ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              required
            />
          </div>

          <div className="space-y-1.5">
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

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="goal-target">
                {form.goal_type === "EXPENSE_LIMIT" ? "Monthly limit (₹)" : "Target amount (₹)"}
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
            <div className="space-y-1.5">
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
            <div className="space-y-1.5">
              <Label htmlFor="goal-category">Linked category (optional)</Label>
              <Input
                id="goal-category"
                placeholder="e.g. Food & Dining"
                value={form.linked_category ?? ""}
                onChange={(e) => setForm((f) => ({ ...f, linked_category: e.target.value || undefined }))}
              />
              <p className="text-xs text-muted-foreground">
                Leave empty to track all spending across categories.
              </p>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="goal-notes">Notes (optional)</Label>
            <Textarea
              id="goal-notes"
              rows={2}
              placeholder="Why this goal matters…"
              value={form.notes ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value || undefined }))}
            />
          </div>

          <Button type="submit" className="w-full" disabled={isPending}>
            {isPending ? "Creating…" : "Create goal"}
          </Button>
        </form>
      </SheetContent>
    </Sheet>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// GoalsSection — the main component
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  className?: string
}

export function GoalsSection({ className }: Props) {
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
          <AddGoalSheet />
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
