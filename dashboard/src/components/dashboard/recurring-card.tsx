/**
 * RecurringCard — shows aggregate recurring transaction stats and top patterns.
 *
 * Phase 4.5c: Recurring Transaction Detection
 *
 * Displays:
 *   - Total monthly fixed costs (OUTFLOW patterns normalised to monthly)
 *   - Total monthly recurring income (INFLOW patterns)
 *   - List of top active recurring outflow patterns (sorted by amount)
 *   - Patterns due this week badge
 *   - "Run Detection" button to trigger the algorithm
 */

"use client"

import * as React from "react"
import { RefreshCw, Repeat, TrendingDown, TrendingUp } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useRecurringPatterns, useRecurringSummary, useRunDetection } from "@/hooks/use-recurring"
import { formatCurrency, cn } from "@/lib/utils"

// ─────────────────────────────────────────────────────────────────────────────
// Frequency label helpers
// ─────────────────────────────────────────────────────────────────────────────

const FREQ_LABELS: Record<string, string> = {
  WEEKLY:    "Weekly",
  MONTHLY:   "Monthly",
  QUARTERLY: "Quarterly",
  YEARLY:    "Yearly",
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  className?: string
}

// Show this many patterns collapsed; user can expand to see all
const COLLAPSED_LIMIT = 5

export function RecurringCard({ className }: Props) {
  const [showAll, setShowAll] = React.useState(false)

  const { data: summary, isLoading: summaryLoading } = useRecurringSummary()
  const { data: patterns, isLoading: patternsLoading } = useRecurringPatterns({
    direction: "OUTFLOW",
    is_active: true,
  })
  const { mutate: detect, isPending: isDetecting } = useRunDetection()

  const sortedPatterns = (patterns ?? [])
    .sort((a, b) => b.expected_amount - a.expected_amount)

  const visiblePatterns = showAll
    ? sortedPatterns
    : sortedPatterns.slice(0, COLLAPSED_LIMIT)

  return (
    <Card className={cn(className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm font-medium">Recurring</CardTitle>
            <p className="text-xs text-muted-foreground">Auto-detected patterns</p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-xs"
            onClick={() => detect()}
            disabled={isDetecting}
          >
            <RefreshCw className={cn("size-3", isDetecting && "animate-spin")} />
            {isDetecting ? "Detecting…" : "Detect"}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Summary stats */}
        {summaryLoading ? (
          <div className="grid grid-cols-2 gap-3">
            <Skeleton className="h-14" />
            <Skeleton className="h-14" />
          </div>
        ) : summary ? (
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border bg-muted/30 p-3">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <TrendingDown className="size-3 text-red-500" />
                Fixed monthly cost
              </div>
              <p className="mt-1 text-base font-semibold tabular-nums">
                {formatCurrency(summary.total_monthly_fixed_cost)}
              </p>
            </div>
            <div className="rounded-lg border bg-muted/30 p-3">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <TrendingUp className="size-3 text-green-500" />
                Recurring income
              </div>
              <p className="mt-1 text-base font-semibold tabular-nums">
                {formatCurrency(summary.total_monthly_recurring_income)}
              </p>
            </div>
          </div>
        ) : null}

        {/* Due this week badge */}
        {summary && summary.patterns_due_this_week > 0 && (
          <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400 text-xs">
            {summary.patterns_due_this_week} pattern{summary.patterns_due_this_week !== 1 ? "s" : ""} due this week
          </Badge>
        )}

        {/* Top patterns list */}
        {patternsLoading ? (
          <div className="space-y-2">
            {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-9" />)}
          </div>
        ) : sortedPatterns.length === 0 ? (
          <div className="text-center py-3 text-xs text-muted-foreground">
            No recurring patterns detected yet.
            <br />
            Click "Detect" to scan your transactions.
          </div>
        ) : (
          <div className="space-y-1.5">
            {visiblePatterns.map((pattern) => (
              <div
                key={pattern.id}
                className="flex items-center justify-between rounded-md px-2 py-1.5 text-xs hover:bg-muted/50"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Repeat className="size-3 shrink-0 text-muted-foreground" />
                  <span className="truncate">{pattern.counterparty}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-muted-foreground">
                    {FREQ_LABELS[pattern.frequency] ?? pattern.frequency}
                  </span>
                  <span className="font-medium tabular-nums">
                    {formatCurrency(pattern.expected_amount)}
                  </span>
                </div>
              </div>
            ))}

            {/* Show all / show less toggle */}
            {sortedPatterns.length > COLLAPSED_LIMIT && (
              <button
                onClick={() => setShowAll((v) => !v)}
                className="w-full pt-1 text-xs text-muted-foreground hover:text-foreground transition-colors text-center"
              >
                {showAll
                  ? "Show less ↑"
                  : `Show all ${sortedPatterns.length} patterns ↓`}
              </button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
