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
  Layers,
  Pencil,
  Plus,
  Target,
  Trash2,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateGoal,
  useDeleteGoal,
  useGoalTree,
  useGoals,
  useLifeEvents,
  useUpdateGoal,
  useUpdateLifeEvent,
} from "@/hooks/use-goals"
import {
  CHART_KEY_EXPENSE_NEED_WANT_STACK,
  CHART_KEY_INVESTMENT_NET,
  categoryChartKey,
} from "@/lib/chart-keys"
import { formatCurrency, cn } from "@/lib/utils"
import type {
  DashboardCategorySeries,
  Goal,
  GoalActivationStatus,
  GoalCreate,
  GoalFundingMode,
  GoalStatus,
  GoalTier,
  GoalTree,
  GoalType,
  GoalUpdate,
  LifeEvent,
  ProgressCadence,
  SensitivityToReturns,
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
// Phase B.5 — pyramid tiers, activation lifecycle, tree helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Maps each API tier bucket to a short label and a left-border accent (Tailwind). */
const TIER_PANELS: {
  treeKey: keyof Pick<GoalTree, "vision" | "strategy" | "tactic" | "operational" | "untiered">
  label: string
  borderClass: string
}[] = [
  { treeKey: "vision", label: "Vision", borderClass: "border-l-violet-500" },
  { treeKey: "strategy", label: "Strategy", borderClass: "border-l-blue-500" },
  { treeKey: "tactic", label: "Tactic", borderClass: "border-l-emerald-600" },
  { treeKey: "operational", label: "Operational", borderClass: "border-l-amber-500" },
  { treeKey: "untiered", label: "Untiered", borderClass: "border-l-muted-foreground" },
]

const ACTIVATION_STATUS_LABELS: Record<string, string> = {
  PENDING: "Pending",
  ACTIVE: "Active",
  COMPLETED: "Completed",
  PAUSED: "Paused",
}

const ACTIVATION_STATUS_STYLES: Record<string, string> = {
  PENDING: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  ACTIVE: "border-green-500/40 bg-green-500/10 text-green-800 dark:text-green-300",
  COMPLETED: "border-blue-500/40 bg-blue-500/10 text-blue-800 dark:text-blue-300",
  PAUSED: "border-muted-foreground/40 bg-muted text-muted-foreground",
}

const GOAL_TIERS: GoalTier[] = ["VISION", "STRATEGY", "TACTIC", "OPERATIONAL"]
const GOAL_TIME_HORIZONS = [
  "MONTHLY",
  "QUARTERLY",
  "ANNUAL",
  "MULTI_YEAR",
  "DECADE",
] as const
const GOAL_FUNDING_MODES: GoalFundingMode[] = [
  "ACCUMULATION",
  "CONSTRAINT",
  "EVENT",
  "MAINTENANCE",
]
const GOAL_ACTIVATION_STATUSES: GoalActivationStatus[] = [
  "PENDING",
  "ACTIVE",
  "COMPLETED",
  "PAUSED",
]
const SENSITIVITY_OPTIONS: SensitivityToReturns[] = ["LOW", "MEDIUM", "HIGH"]

/** Build id → goal for every node returned in GET /api/goals/tree. */
function goalsByIdFromTree(tree: GoalTree): Map<number, Goal> {
  const m = new Map<number, Goal>()
  for (const g of tree.vision) m.set(g.id, g)
  for (const g of tree.strategy) m.set(g.id, g)
  for (const g of tree.tactic) m.set(g.id, g)
  for (const g of tree.operational) m.set(g.id, g)
  for (const g of tree.untiered) m.set(g.id, g)
  return m
}

/**
 * Parent goals (higher pyramid) point *to* children via GoalLink rows.
 * For a given goal, list human-readable parent labels for "Feeds:" lines.
 */
function parentLabelsForGoal(goalId: number, tree: GoalTree, byId: Map<number, Goal>): string[] {
  const labels: string[] = []
  for (const link of tree.links) {
    if (link.child_goal_id !== goalId) continue
    const p = byId.get(link.parent_goal_id)
    if (p) {
      labels.push(p.pyramid_id ? `${p.pyramid_id} · ${p.name}` : p.name)
    }
  }
  return labels
}

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
  // Phase B — pyramid / activation (optional on legacy goals)
  const [pyramidId, setPyramidId] = React.useState(goal.pyramid_id ?? "")
  const [tier, setTier] = React.useState(goal.tier ?? "")
  const [timeHorizon, setTimeHorizon] = React.useState(goal.time_horizon ?? "")
  const [fundingMode, setFundingMode] = React.useState(goal.funding_mode ?? "")
  const [activationStatus, setActivationStatus] = React.useState(
    (goal.activation_status as GoalActivationStatus | undefined) ?? "ACTIVE",
  )
  const [monthlyAllocation, setMonthlyAllocation] = React.useState(
    goal.monthly_allocation != null ? String(goal.monthly_allocation) : "",
  )
  const [allocationPriority, setAllocationPriority] = React.useState(
    goal.allocation_priority != null ? String(goal.allocation_priority) : "",
  )
  const [activationCondition, setActivationCondition] = React.useState(
    goal.activation_condition ?? "",
  )
  const [interruptible, setInterruptible] = React.useState(goal.interruptible !== false)
  const [sensitivity, setSensitivity] = React.useState(goal.sensitivity_to_returns ?? "")

  React.useEffect(() => {
    if (!open) return
    setName(goal.name)
    setTargetAmount(goal.target_amount != null ? String(goal.target_amount) : "")
    setTargetDate(goal.target_date ?? "")
    setLinkedCategory(goal.linked_category ?? "")
    setExpenseChartBind(goal.chart_key ?? "")
    setProgressCadence(goal.progress_cadence ?? "MONTHLY")
    setNotes(goal.notes ?? "")
    setPyramidId(goal.pyramid_id ?? "")
    setTier(goal.tier ?? "")
    setTimeHorizon(goal.time_horizon ?? "")
    setFundingMode(goal.funding_mode ?? "")
    setActivationStatus((goal.activation_status as GoalActivationStatus | undefined) ?? "ACTIVE")
    setMonthlyAllocation(goal.monthly_allocation != null ? String(goal.monthly_allocation) : "")
    setAllocationPriority(goal.allocation_priority != null ? String(goal.allocation_priority) : "")
    setActivationCondition(goal.activation_condition ?? "")
    setInterruptible(goal.interruptible !== false)
    setSensitivity(goal.sensitivity_to_returns ?? "")
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

    // Pyramid & activation — send null when cleared so the API can unset optional fields
    update.pyramid_id = pyramidId.trim() ? pyramidId.trim() : null
    update.tier = tier.trim() ? tier.trim().toUpperCase() : null
    update.time_horizon = timeHorizon.trim() ? timeHorizon.trim().toUpperCase() : null
    update.funding_mode = fundingMode.trim() ? fundingMode.trim().toUpperCase() : null
    update.activation_status = activationStatus
    update.activation_condition = activationCondition.trim() ? activationCondition.trim() : null
    update.interruptible = interruptible
    update.sensitivity_to_returns = sensitivity.trim() ? sensitivity.trim().toUpperCase() : null
    if (monthlyAllocation.trim() === "") {
      update.monthly_allocation = null
    } else {
      const ma = parseFloat(monthlyAllocation)
      if (!Number.isNaN(ma)) update.monthly_allocation = ma
    }
    if (allocationPriority.trim() === "") {
      update.allocation_priority = null
    } else {
      const ap = parseInt(allocationPriority, 10)
      if (!Number.isNaN(ap)) update.allocation_priority = ap
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

          <div className="rounded-md border border-dashed p-3 space-y-4">
            <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
              <Layers className="size-3.5" />
              Pyramid &amp; activation
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-pyramid-${goal.id}`}>Pyramid id</Label>
                <Input
                  id={`edit-pyramid-${goal.id}`}
                  placeholder="e.g. V1"
                  maxLength={10}
                  value={pyramidId}
                  onChange={(e) => setPyramidId(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-tier-${goal.id}`}>Tier</Label>
                <Select
                  value={tier || "__none__"}
                  onValueChange={(v) => setTier(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id={`edit-tier-${goal.id}`}>
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {GOAL_TIERS.map((t) => (
                      <SelectItem key={t} value={t}>{t}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-horizon-${goal.id}`}>Time horizon</Label>
                <Select
                  value={timeHorizon || "__none__"}
                  onValueChange={(v) => setTimeHorizon(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id={`edit-horizon-${goal.id}`}>
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {GOAL_TIME_HORIZONS.map((h) => (
                      <SelectItem key={h} value={h}>{h.replaceAll("_", " ")}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-funding-${goal.id}`}>Funding mode</Label>
                <Select
                  value={fundingMode || "__none__"}
                  onValueChange={(v) => setFundingMode(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id={`edit-funding-${goal.id}`}>
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {GOAL_FUNDING_MODES.map((f) => (
                      <SelectItem key={f} value={f}>{f}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-act-${goal.id}`}>Activation status</Label>
                <Select
                  value={activationStatus}
                  onValueChange={(v) => {
                    if (v) setActivationStatus(v as GoalActivationStatus)
                  }}
                >
                  <SelectTrigger id={`edit-act-${goal.id}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {GOAL_ACTIVATION_STATUSES.map((s) => (
                      <SelectItem key={s} value={s}>{ACTIVATION_STATUS_LABELS[s] ?? s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-sens-${goal.id}`}>Sensitivity to returns</Label>
                <Select
                  value={sensitivity || "__none__"}
                  onValueChange={(v) => setSensitivity(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id={`edit-sens-${goal.id}`}>
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {SENSITIVITY_OPTIONS.map((s) => (
                      <SelectItem key={s} value={s}>{s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-moalloc-${goal.id}`}>Monthly allocation (₹)</Label>
                <Input
                  id={`edit-moalloc-${goal.id}`}
                  type="number"
                  min={0}
                  placeholder="Optional"
                  value={monthlyAllocation}
                  onChange={(e) => setMonthlyAllocation(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor={`edit-prio-${goal.id}`}>Allocation priority (1–100)</Label>
                <Input
                  id={`edit-prio-${goal.id}`}
                  type="number"
                  min={1}
                  max={100}
                  placeholder="Optional"
                  value={allocationPriority}
                  onChange={(e) => setAllocationPriority(e.target.value)}
                />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor={`edit-actcond-${goal.id}`}>Activation condition (DSL)</Label>
              <Textarea
                id={`edit-actcond-${goal.id}`}
                rows={2}
                className="font-mono text-xs"
                placeholder='e.g. goal:S5:completed AND goal:S4:completed'
                value={activationCondition}
                onChange={(e) => setActivationCondition(e.target.value)}
              />
              <p className="text-[11px] text-muted-foreground leading-snug">
                When <span className="font-medium">activation status</span> is Pending, the API evaluates this expression
                against your goals&apos; activation states and life events. Atoms:{" "}
                <code className="rounded bg-muted px-0.5">goal:PYRAMID_ID:status</code>{" "}
                (status: pending, active, completed, paused) and{" "}
                <code className="rounded bg-muted px-0.5">event:key</code>. Combine with{" "}
                <code className="rounded bg-muted px-0.5">AND</code> /{" "}
                <code className="rounded bg-muted px-0.5">OR</code> and parentheses. Max 500 characters.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id={`edit-interrupt-${goal.id}`}
                checked={interruptible}
                onCheckedChange={(c) => setInterruptible(c === true)}
              />
              <Label htmlFor={`edit-interrupt-${goal.id}`} className="text-sm font-normal cursor-pointer">
                Interruptible (safe to pause if surplus drops)
              </Label>
            </div>
          </div>

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

function GoalCard({
  goal,
  hierarchyMeta,
  tierBorderClass,
}: {
  goal: Goal
  /** When set, show pyramid / activation badges and optional "Feeds:" parents (hierarchy tab). */
  hierarchyMeta?: { parentLabels: string[] }
  /** e.g. border-l-violet-500 — applied as thick left stripe in hierarchy view */
  tierBorderClass?: string
}) {
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

  const act = (goal.activation_status ?? "ACTIVE").toUpperCase()

  return (
    <div
      className={cn(
        "rounded-lg border bg-card px-4 py-3 space-y-2.5",
        hierarchyMeta && tierBorderClass && `border-l-4 ${tierBorderClass}`,
      )}
    >
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
            {hierarchyMeta && hierarchyMeta.parentLabels.length > 0 && (
              <p className="text-[11px] text-muted-foreground mt-1">
                <span className="font-medium text-foreground/80">Feeds:</span>{" "}
                {hierarchyMeta.parentLabels.join(" · ")}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
          {hierarchyMeta && goal.pyramid_id && (
            <Badge variant="secondary" className="text-[10px] px-1 py-0 font-mono">
              {goal.pyramid_id}
            </Badge>
          )}
          {hierarchyMeta && (
            <Badge
              variant="outline"
              className={cn(
                "text-[10px] px-1 py-0",
                ACTIVATION_STATUS_STYLES[act] ?? "border-muted",
              )}
            >
              {ACTIVATION_STATUS_LABELS[act] ?? act}
            </Badge>
          )}
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
})

function AddGoalSheet({ prefillChartKey }: { prefillChartKey?: string | null }) {
  const [open, setOpen] = React.useState(false)
  const { mutate: create, isPending } = useCreateGoal()

  const [form, setForm] = React.useState<Partial<GoalCreate>>(defaultAddForm())
  /** When set, create/update EXPENSE_LIMIT with this chart_key (else use linked_category). */
  const [expenseChartBind, setExpenseChartBind] = React.useState("")
  const [expenseCadence, setExpenseCadence] = React.useState<ProgressCadence>("MONTHLY")
  const [pyramidId, setPyramidId] = React.useState("")
  const [tier, setTier] = React.useState("")
  const [timeHorizon, setTimeHorizon] = React.useState("")
  const [fundingMode, setFundingMode] = React.useState("")
  const [activationStatus, setActivationStatus] = React.useState<GoalActivationStatus>("ACTIVE")
  const [monthlyAllocation, setMonthlyAllocation] = React.useState("")
  const [allocationPriority, setAllocationPriority] = React.useState("")
  const [activationCondition, setActivationCondition] = React.useState("")
  const [interruptible, setInterruptible] = React.useState(true)
  const [sensitivity, setSensitivity] = React.useState("")

  function resetAddForm() {
    setForm(defaultAddForm())
    setExpenseChartBind("")
    setExpenseCadence("MONTHLY")
    setPyramidId("")
    setTier("")
    setTimeHorizon("")
    setFundingMode("")
    setActivationStatus("ACTIVE")
    setMonthlyAllocation("")
    setAllocationPriority("")
    setActivationCondition("")
    setInterruptible(true)
    setSensitivity("")
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
      notes: form.notes,
    }

    if (pyramidId.trim()) base.pyramid_id = pyramidId.trim()
    if (tier) base.tier = tier
    if (timeHorizon) base.time_horizon = timeHorizon
    if (fundingMode) base.funding_mode = fundingMode
    base.activation_status = activationStatus
    if (activationCondition.trim()) base.activation_condition = activationCondition.trim()
    base.interruptible = interruptible
    if (sensitivity) base.sensitivity_to_returns = sensitivity
    if (monthlyAllocation.trim()) {
      const ma = parseFloat(monthlyAllocation)
      if (!Number.isNaN(ma)) base.monthly_allocation = ma
    }
    if (allocationPriority.trim()) {
      const ap = parseInt(allocationPriority, 10)
      if (!Number.isNaN(ap)) base.allocation_priority = ap
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

          <div className="rounded-md border border-dashed p-3 space-y-4">
            <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
              <Layers className="size-3.5" />
              Pyramid &amp; activation (optional)
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-pyramid">Pyramid id</Label>
                <Input
                  id="add-pyramid"
                  placeholder="e.g. O5"
                  maxLength={10}
                  value={pyramidId}
                  onChange={(e) => setPyramidId(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-tier">Tier</Label>
                <Select
                  value={tier || "__none__"}
                  onValueChange={(v) => setTier(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id="add-tier">
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {GOAL_TIERS.map((t) => (
                      <SelectItem key={t} value={t}>{t}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-horizon">Time horizon</Label>
                <Select
                  value={timeHorizon || "__none__"}
                  onValueChange={(v) => setTimeHorizon(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id="add-horizon">
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {GOAL_TIME_HORIZONS.map((h) => (
                      <SelectItem key={h} value={h}>{h.replaceAll("_", " ")}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-funding">Funding mode</Label>
                <Select
                  value={fundingMode || "__none__"}
                  onValueChange={(v) => setFundingMode(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id="add-funding">
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {GOAL_FUNDING_MODES.map((f) => (
                      <SelectItem key={f} value={f}>{f}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-act">Activation status</Label>
                <Select
                  value={activationStatus}
                  onValueChange={(v) => {
                    if (v) setActivationStatus(v as GoalActivationStatus)
                  }}
                >
                  <SelectTrigger id="add-act">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {GOAL_ACTIVATION_STATUSES.map((s) => (
                      <SelectItem key={s} value={s}>{ACTIVATION_STATUS_LABELS[s] ?? s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-sens">Sensitivity to returns</Label>
                <Select
                  value={sensitivity || "__none__"}
                  onValueChange={(v) => setSensitivity(!v || v === "__none__" ? "" : v)}
                >
                  <SelectTrigger id="add-sens">
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    {SENSITIVITY_OPTIONS.map((s) => (
                      <SelectItem key={s} value={s}>{s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-moalloc">Monthly allocation (₹)</Label>
                <Input
                  id="add-moalloc"
                  type="number"
                  min={0}
                  placeholder="Optional"
                  value={monthlyAllocation}
                  onChange={(e) => setMonthlyAllocation(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="add-prio">Allocation priority (1–100)</Label>
                <Input
                  id="add-prio"
                  type="number"
                  min={1}
                  max={100}
                  placeholder="Optional"
                  value={allocationPriority}
                  onChange={(e) => setAllocationPriority(e.target.value)}
                />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="add-actcond">Activation condition (DSL)</Label>
              <Textarea
                id="add-actcond"
                rows={2}
                className="font-mono text-xs"
                placeholder="Leave empty unless this goal should wait on others"
                value={activationCondition}
                onChange={(e) => setActivationCondition(e.target.value)}
              />
              <p className="text-[11px] text-muted-foreground leading-snug">
                Use <code className="rounded bg-muted px-0.5">goal:ID:completed</code>,{" "}
                <code className="rounded bg-muted px-0.5">event:employed</code>, with{" "}
                <code className="rounded bg-muted px-0.5">AND</code> /{" "}
                <code className="rounded bg-muted px-0.5">OR</code>. Validated on save.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="add-interrupt"
                checked={interruptible}
                onCheckedChange={(c) => setInterruptible(c === true)}
              />
              <Label htmlFor="add-interrupt" className="text-sm font-normal cursor-pointer">
                Interruptible
              </Label>
            </div>
          </div>

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

/** Renders tier buckets from GET /api/goals/tree with parent “Feeds:” labels. */
function GoalsHierarchyPanels({ tree }: { tree: GoalTree }) {
  const byId = React.useMemo(() => goalsByIdFromTree(tree), [tree])
  const totalInBuckets = TIER_PANELS.reduce((n, p) => n + tree[p.treeKey].length, 0)

  if (totalInBuckets === 0) {
    return (
      <p className="text-sm text-muted-foreground py-6 text-center">
        No goals returned from the hierarchy endpoint.
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {TIER_PANELS.map(({ treeKey, label, borderClass }) => {
        const bucket = tree[treeKey]
        if (bucket.length === 0) return null
        return (
          <details key={treeKey} open className="rounded-lg border bg-card/30">
            <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium list-none flex items-center justify-between gap-2 [&::-webkit-details-marker]:hidden">
              <span>{label}</span>
              <Badge variant="secondary" className="text-[10px] font-normal">
                {bucket.length}
              </Badge>
            </summary>
            <div className="space-y-2 px-2 pb-3 pt-1">
              {bucket.map((g) => (
                <GoalCard
                  key={g.id}
                  goal={g}
                  hierarchyMeta={{ parentLabels: parentLabelsForGoal(g.id, tree, byId) }}
                  tierBorderClass={borderClass}
                />
              ))}
            </div>
          </details>
        )
      })}
    </div>
  )
}

function formatEventKeyLabel(key: string): string {
  return key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Checkboxes for life-event rows — flipping &quot;occurred&quot; can auto-activate pending goals server-side. */
function LifeEventsPanel({ events }: { events: LifeEvent[] }) {
  const { mutate, isPending } = useUpdateLifeEvent()

  if (events.length === 0) {
    return (
      <p className="text-xs text-muted-foreground px-1 py-2">
        No life events yet. They are created by the seed script or API and referenced in activation conditions
        as <code className="rounded bg-muted px-0.5">event:key</code>.
      </p>
    )
  }

  return (
    <ul className="space-y-2 px-1 py-1">
      {events.map((ev) => (
        <li key={ev.id} className="flex items-start gap-2 text-sm">
          <Checkbox
            id={`life-ev-${ev.id}`}
            checked={ev.occurred}
            disabled={isPending}
            onCheckedChange={(c) => {
              const checked = c === true
              mutate({
                id: ev.id,
                update: {
                  occurred: checked,
                  occurred_date: checked
                    ? new Date().toISOString().slice(0, 10)
                    : null,
                },
              })
            }}
          />
          <div className="min-w-0 flex-1">
            <Label htmlFor={`life-ev-${ev.id}`} className="font-medium cursor-pointer leading-tight">
              {formatEventKeyLabel(ev.event_key)}
            </Label>
            <p className="text-[11px] text-muted-foreground font-mono mt-0.5">{ev.event_key}</p>
            {ev.occurred_date && (
              <p className="text-[11px] text-muted-foreground">Date: {ev.occurred_date}</p>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}

export function GoalsSection({ className, initialChartKey = null }: Props) {
  const { data: goals, isLoading } = useGoals()
  const { data: tree, isLoading: treeLoading, isError: treeError } = useGoalTree()
  const { data: lifeEvents } = useLifeEvents()

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

      <CardContent>
        <Tabs defaultValue="flat" className="w-full">
          <TabsList variant="line" className="mb-3 h-8 w-full min-w-0 justify-start">
            <TabsTrigger value="flat" className="text-xs">
              Flat
            </TabsTrigger>
            <TabsTrigger value="hierarchy" className="text-xs gap-1">
              <Layers className="size-3" />
              Hierarchy
            </TabsTrigger>
          </TabsList>

          <TabsContent value="flat" className="space-y-2 mt-0">
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
          </TabsContent>

          <TabsContent value="hierarchy" className="space-y-3 mt-0">
            {treeLoading ? (
              <div className="space-y-3">
                {[...Array(4)].map((_, i) => (
                  <Skeleton key={i} className="h-24" />
                ))}
              </div>
            ) : treeError ? (
              <p className="text-sm text-destructive">
                Could not load goal tree. Check that the API is running and you are logged in.
              </p>
            ) : tree ? (
              <GoalsHierarchyPanels tree={tree} />
            ) : null}

            <details className="rounded-lg border text-sm">
              <summary className="cursor-pointer select-none px-3 py-2 font-medium text-muted-foreground list-none [&::-webkit-details-marker]:hidden">
                Life events (activation DSL)
              </summary>
              <LifeEventsPanel events={lifeEvents ?? []} />
            </details>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}
