/**
 * SummaryCards — the four headline metric cards at the top of the dashboard.
 *
 * Cards:
 *   1. Total Inflow     (green arrow up = good)
 *   2. Total Outflow   (green arrow down = less outflow is good)
 *   3. Savings          (total_savings = invested in Asset Markets; green arrow up = good)
 *   4. Savings Rate     (invested % of income; green arrow up = good)
 *
 * Each card shows:
 *   - The metric value for the *current* period
 *   - A delta vs the *previous* period (as % change, with coloured arrow)
 *   - A loading skeleton while data is in flight
 *
 * Props:
 *   currentRange   — the active DateRange (used to fetch current metrics)
 *   previousRange  — the period before it (used to compute MoM deltas)
 */

"use client"

import {
  TrendingUp,
  TrendingDown,
  Minus,
  ArrowDownLeft,
  ArrowUpRight,
  Wallet,
  PiggyBank,
} from "lucide-react"

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useMetricsSummary } from "@/hooks/use-metrics"
import { formatCurrency, formatPercent, cn } from "@/lib/utils"
import type { DateRange } from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// Delta helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Computes the percentage change from previous → current.
 * Returns null if previous is 0 (division by zero — we can't compute a rate).
 */
function pctChange(current: number, previous: number): number | null {
  if (previous === 0) return null
  return ((current - previous) / Math.abs(previous)) * 100
}

interface DeltaBadgeProps {
  current: number
  previous: number
  /** If true, *lower* is better (e.g. expenses). The delta colour flips. */
  lowerIsBetter?: boolean
  /** If true, values are already percentages — show absolute diff instead of %. */
  isPercentage?: boolean
}

function DeltaBadge({ current, previous, lowerIsBetter = false, isPercentage = false }: DeltaBadgeProps) {
  const rawDelta  = current - previous
  const pctDelta  = pctChange(current, previous)

  if (pctDelta === null) {
    return <span className="text-xs text-muted-foreground">— no prior data</span>
  }

  // Is this change directionally *good* for the user?
  const isGood = lowerIsBetter ? rawDelta < 0 : rawDelta > 0
  const isFlat = Math.abs(pctDelta) < 0.1

  const Icon = isFlat ? Minus : isGood ? TrendingUp : TrendingDown
  const colorClass = isFlat
    ? "text-muted-foreground"
    : isGood
    ? "text-emerald-500"
    : "text-rose-500"

  // For savings rate, show the absolute point difference (e.g. "+3.2 pp")
  // For everything else, show the percentage change.
  const label = isPercentage
    ? `${rawDelta >= 0 ? "+" : ""}${rawDelta.toFixed(1)} pp`
    : `${pctDelta >= 0 ? "+" : ""}${Math.abs(pctDelta).toFixed(1)}%`

  return (
    <div className={cn("flex items-center gap-1 text-xs font-medium", colorClass)}>
      <Icon className="size-3.5 shrink-0" />
      <span>{label}</span>
      <span className="font-normal text-muted-foreground">vs prior period</span>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Single card skeleton (shown during loading)
// ─────────────────────────────────────────────────────────────────────────────

function MetricCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-4 w-24" />
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-4 w-40" />
      </CardContent>
    </Card>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

interface SummaryCardsProps {
  currentRange: DateRange
  previousRange: DateRange
}

export function SummaryCards({ currentRange, previousRange }: SummaryCardsProps) {
  const { data: current, isLoading: loadingCurrent, isError: errorCurrent } =
    useMetricsSummary(currentRange)

  const { data: previous, isLoading: loadingPrev } =
    useMetricsSummary(previousRange)

  // Show skeletons while the *current* period is loading
  if (loadingCurrent) {
    return (
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCardSkeleton />
        <MetricCardSkeleton />
        <MetricCardSkeleton />
        <MetricCardSkeleton />
      </div>
    )
  }

  if (errorCurrent || !current) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-sm text-destructive">
        Failed to load metrics. Check that the FastAPI backend is running on port 8000.
      </div>
    )
  }

  const prev = previous // may be undefined if still loading — DeltaBadge handles that

  // Savings (invested amount) is always non-negative; use emerald for consistency
  const savingsColor = "text-emerald-500"

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">

      {/* ── Inflow ──────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-sm font-medium text-muted-foreground">
            Total Inflow
            <ArrowDownLeft className="size-4 text-emerald-500" />
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1.5">
          <p className="text-2xl font-bold tabular-nums">
            {formatCurrency(current.total_income)}
          </p>
          {!loadingPrev && prev ? (
            <DeltaBadge
              current={current.total_income}
              previous={prev.total_income}
            />
          ) : (
            <Skeleton className="h-4 w-36" />
          )}
        </CardContent>
      </Card>

      {/* ── Outflow ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-sm font-medium text-muted-foreground">
            Total Outflow
            <ArrowUpRight className="size-4 text-rose-500" />
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1.5">
          <p className="text-2xl font-bold tabular-nums">
            {formatCurrency(current.total_expense)}
          </p>
          {!loadingPrev && prev ? (
            <DeltaBadge
              current={current.total_expense}
              previous={prev.total_expense}
              lowerIsBetter
            />
          ) : (
            <Skeleton className="h-4 w-36" />
          )}
        </CardContent>
      </Card>

      {/* ── Savings (invested in Asset Markets) ────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-sm font-medium text-muted-foreground">
            Savings
            <Wallet className="size-4 text-muted-foreground" />
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1.5">
          <p className={cn("text-2xl font-bold tabular-nums", savingsColor)}>
            {formatCurrency(current.total_savings)}
          </p>
          <p className="text-xs text-muted-foreground">
            invested (Asset Markets)
          </p>
          {!loadingPrev && prev ? (
            <DeltaBadge
              current={current.total_savings}
              previous={prev.total_savings}
            />
          ) : (
            <Skeleton className="h-4 w-36" />
          )}
        </CardContent>
      </Card>

      {/* ── Savings Rate (invested % of income) ─────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-sm font-medium text-muted-foreground">
            Savings Rate
            <PiggyBank className="size-4 text-muted-foreground" />
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1.5">
          <p className="text-2xl font-bold tabular-nums">
            {formatPercent(current.savings_rate)}
          </p>
          <p className="text-xs text-muted-foreground">
            % of income invested
          </p>
          {!loadingPrev && prev ? (
            <DeltaBadge
              current={current.savings_rate}
              previous={prev.savings_rate}
              isPercentage
            />
          ) : (
            <Skeleton className="h-4 w-36" />
          )}
        </CardContent>
      </Card>

    </div>
  )
}
