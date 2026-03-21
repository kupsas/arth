"use client"

import * as React from "react"
import { Check, X } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useGoalProgress } from "@/hooks/use-metrics"
import { CATEGORY_CHART_PREFIX } from "@/lib/chart-keys"
import { formatCurrency, formatMonthShort } from "@/lib/utils"
import type {
  DashboardCategorySeries,
  Goal,
  GoalProgressAdherenceMonth,
  GoalProgressResponse,
  ProgressCadence,
} from "@/lib/types"
import Link from "next/link"

/** Maps category series to globals.css --chart-cat-* tokens (same as CATEGORY_SERIES_COLOR). */
const SERIES_CSS_VAR: Record<DashboardCategorySeries, string> = {
  swiggy_instamart: "--chart-cat-1",
  swiggy_food: "--chart-cat-2",
  food_and_dining: "--chart-cat-3",
  gifts: "--chart-cat-4",
  shopping: "--chart-cat-5",
  transport: "--chart-cat-6",
  travel: "--chart-cat-7",
}

/** Which :root token to use for the progress arc (must match trend chart colours). */
function donutProgressCssVar(
  goalType: string,
  chartKey: string | null | undefined,
  pct: number,
  hasTarget: boolean,
): string {
  if (!hasTarget) return "--border"
  if (goalType === "INVESTMENT") return "--chart-purchase"
  if (pct > 100) return "--chart-sale"
  if (chartKey?.startsWith(CATEGORY_CHART_PREFIX)) {
    const series = chartKey.slice(CATEGORY_CHART_PREFIX.length) as DashboardCategorySeries
    if (series in SERIES_CSS_VAR) return SERIES_CSS_VAR[series]
  }
  return "--chart-need"
}

function periodCopy(c: ProgressCadence | undefined): string {
  return (c ?? "MONTHLY") === "ANNUAL"
    ? " this year (Jan — today)"
    : " this month"
}

/**
 * Donut = two SVG stroke rings. Use `stroke="currentColor"` plus `<g style={{ color: 'var(--token)' }}>`
 * so theme vars resolve in the cascade (raw `stroke="var(--x)"` is flaky in some SVG stacks).
 */
function GoalDonut({
  pct,
  hasTarget,
  goalType,
  chartKey,
}: {
  pct: number
  hasTarget: boolean
  goalType: string
  chartKey: string | null | undefined
}) {
  const displayPct = Math.round(pct)
  const ringPct = hasTarget ? Math.min(100, Math.max(0, pct)) : 0

  const progressToken = donutProgressCssVar(goalType, chartKey, pct, hasTarget)

  const vb = 100
  const strokeW = 12
  const r = (vb - strokeW) / 2
  const c = vb / 2
  const circumference = 2 * Math.PI * r
  const dashLen = (ringPct / 100) * circumference

  return (
    <div className="relative mx-auto size-[100px] shrink-0">
      <svg
        width={100}
        height={100}
        viewBox={`0 0 ${vb} ${vb}`}
        className="block -rotate-90"
        aria-hidden
      >
        <g style={{ color: "var(--border)" }}>
          <circle
            cx={c}
            cy={c}
            r={r}
            fill="none"
            stroke="currentColor"
            strokeWidth={strokeW}
          />
        </g>
        {hasTarget && ringPct > 0 && (
          <g style={{ color: `var(${progressToken})` }}>
            <circle
              cx={c}
              cy={c}
              r={r}
              fill="none"
              stroke="currentColor"
              strokeWidth={strokeW}
              strokeLinecap="round"
              strokeDasharray={`${dashLen} ${circumference}`}
            />
          </g>
        )}
      </svg>
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
        <span className="text-sm font-semibold tabular-nums text-foreground">
          {hasTarget ? `${displayPct}%` : "—"}
        </span>
      </div>
    </div>
  )
}

function adherenceTooltipLine(
  m: GoalProgressAdherenceMonth,
  target: number | null,
): string {
  const cap = target != null && target > 0 ? target : null
  const raw =
    m.amount != null && Number.isFinite(Number(m.amount)) ? Number(m.amount) : null
  if (cap != null && raw != null) {
    const pct = Math.min(500, Math.round((raw / cap) * 100))
    return `${formatCurrency(raw)}/${formatCurrency(cap)} (${pct}%)`
  }
  if (cap == null && m.hit == null) {
    return "Set a target on Goals to track ✓ / ✗."
  }
  if (raw != null) return formatCurrency(raw)
  return "—"
}

function AdherenceCell({
  m,
  target,
}: {
  m: GoalProgressAdherenceMonth
  target: number | null
}) {
  const monthShort = formatMonthShort(m.month)
  const tooltipText = adherenceTooltipLine(m, target)

  return (
    <Tooltip>
      <TooltipTrigger className="flex flex-col items-center gap-1 rounded-md px-1 py-0.5 text-center outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring">
        <span className="flex size-7 items-center justify-center rounded border border-border bg-muted/40">
          {m.hit == null ? (
            <span className="text-[10px] text-muted-foreground">—</span>
          ) : m.hit ? (
            <Check className="size-3.5 text-emerald-600" aria-hidden />
          ) : (
            <X className="size-3.5 text-rose-600" aria-hidden />
          )}
        </span>
        <span className="max-w-13 truncate text-[10px] leading-tight text-muted-foreground">
          {monthShort}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-[280px] text-left">
        <p>{tooltipText}</p>
      </TooltipContent>
    </Tooltip>
  )
}

function SingleGoalBar({ goal }: { goal: Goal }) {
  const { data, isLoading, isError } = useGoalProgress(goal.id)

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border border-border/80 bg-muted/10 p-3">
        <Skeleton className="h-4 w-4/5" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="mx-auto size-[100px] rounded-full" />
        <Skeleton className="mx-auto h-12 w-44" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="rounded-lg border border-border/80 p-3">
        <p className="text-sm text-muted-foreground">
          Could not load progress for this goal.
        </p>
      </div>
    )
  }

  const cadence = data.progress_cadence ?? "MONTHLY"
  const target = data.target_amount
  const hasTarget = target != null && target > 0
  const pct = hasTarget
    ? Math.min(500, (data.current_value / target!) * 100)
    : 0

  return (
    <GoalProgressBody
      data={data}
      title={goal.name}
      chartKey={goal.chart_key}
      cadence={cadence}
      pct={pct}
      target={target}
      hasTarget={hasTarget}
    />
  )
}

function GoalProgressBody({
  data,
  title,
  chartKey,
  cadence,
  pct,
  target,
  hasTarget,
}: {
  data: GoalProgressResponse
  title: string
  chartKey: string | null | undefined
  cadence: ProgressCadence
  pct: number
  target: number | null
  hasTarget: boolean
}) {
  const p = periodCopy(cadence)

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/80 bg-muted/10 p-3">
      <div className="w-full text-left">
        <h3 className="text-sm font-semibold leading-snug">{title}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {data.goal_type === "EXPENSE_LIMIT" ? (
            <>
              {formatCurrency(data.current_value)} spent
              {hasTarget ? (
                <>
                  {" "}
                  of {formatCurrency(target!)} cap{p}
                </>
              ) : (
                <>{p} (set a target on the Goals page)</>
              )}
            </>
          ) : (
            <>
              {formatCurrency(data.current_value)}
              {hasTarget ? (
                <> of {formatCurrency(target!)} net target{p}</>
              ) : (
                <>{p} (set a target on the Goals page)</>
              )}
            </>
          )}
        </p>
        {cadence === "ANNUAL" && data.goal_type === "EXPENSE_LIMIT" && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            Annual cap: spend is summed from 1 Jan through today. Monthly ✓/✗ row is
            hidden.
          </p>
        )}
      </div>

      <div className="flex justify-center py-1">
        <GoalDonut
          pct={pct}
          hasTarget={hasTarget}
          goalType={data.goal_type}
          chartKey={chartKey}
        />
      </div>

      {data.adherence.length > 0 && (
        <div className="mt-1 flex flex-col items-center gap-2 border-t border-border/60 pt-3">
          <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Last 4 months
          </p>
          <div className="flex flex-wrap items-start justify-center gap-2 sm:gap-3">
            {data.adherence.map((m) => (
              <AdherenceCell key={m.month} m={m} target={target} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Expense + investment goals evaluated monthly (excludes ANNUAL expense limits). */
function headlineTrackedGoals(goals: Goal[] | undefined): Goal[] {
  if (!goals?.length) return []
  return goals
    .filter((g) => {
      if (g.goal_type !== "EXPENSE_LIMIT" && g.goal_type !== "INVESTMENT") {
        return false
      }
      if (g.goal_type === "EXPENSE_LIMIT" && (g.progress_cadence ?? "MONTHLY") === "ANNUAL") {
        return false
      }
      return true
    })
    .sort((a, b) => a.priority - b.priority || a.id - b.id)
}

/**
 * All auto-tracked **monthly** goals (every EXPENSE_LIMIT except ANNUAL, plus every INVESTMENT).
 */
export function GoalProgressSection({ goals }: { goals: Goal[] | undefined }) {
  const tracked = React.useMemo(() => headlineTrackedGoals(goals), [goals])

  if (tracked.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Progress on Goals</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No monthly <strong>INVESTMENT</strong> or <strong>EXPENSE_LIMIT</strong> goals yet (annual
            expense caps stay on the{" "}
            <Link href="/goals" className="text-foreground underline underline-offset-2">
              Goals
            </Link>{" "}
            page).{" "}
            <Link href="/goals" className="text-foreground underline underline-offset-2">
              Create one
            </Link>
            .
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Progress on Goals</CardTitle>
        <p className="text-xs font-normal text-muted-foreground">
          Donut = progress this period. Below each goal: last four months with month labels — hover
          ✓/✗ for net or spend in that month.
        </p>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        {tracked.map((g) => (
          <SingleGoalBar key={g.id} goal={g} />
        ))}
      </CardContent>
    </Card>
  )
}
