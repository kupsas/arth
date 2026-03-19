/**
 * SpendingBreakdownChart — donut chart showing NEED / WANT / INVESTMENT
 * macro breakdown of outflow spending.
 *
 * Phase 4.5c: Needs / Wants / Investments tagging
 *
 * Shows what share of your spending goes to:
 *   NEED         — rent, bills, healthcare, transport (essential)
 *   WANT         — dining, entertainment, shopping, travel (discretionary)
 *   INVESTMENT   — equities, mutual funds, self-transfers to savings
 *   UNCLASSIFIED — not yet tagged by the pipeline
 *
 * Data source: useSpendCategoryBreakdown(dateRange)
 */

"use client"

import * as React from "react"
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useSpendCategoryBreakdown } from "@/hooks/use-metrics"
import { formatCurrency, cn } from "@/lib/utils"
import type { DateRange, SpendCategory } from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// Colour palette for each spend category
// ─────────────────────────────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<SpendCategory | "UNCLASSIFIED", string> = {
  NEED:          "#3b82f6",   // blue   — essential, stable
  WANT:          "#f97316",   // orange — discretionary, warm
  INVESTMENT:    "#a855f7",   // purple — wealth building
  UNCLASSIFIED:  "#6b7280",   // grey   — not yet tagged
}

const CATEGORY_LABELS: Record<SpendCategory | "UNCLASSIFIED", string> = {
  NEED:          "Needs",
  WANT:          "Wants",
  INVESTMENT:    "Investments",
  UNCLASSIFIED:  "Unclassified",
}

// ─────────────────────────────────────────────────────────────────────────────
// Custom tooltip
// ─────────────────────────────────────────────────────────────────────────────

interface TooltipEntry {
  payload?: {
    spend_category: string
    amount: number
    percentage: number
    txn_count: number
  }
}

function SpendTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: TooltipEntry[]
}) {
  if (!active || !payload?.length || !payload[0]?.payload) return null
  const d = payload[0].payload
  const label = CATEGORY_LABELS[d.spend_category as SpendCategory | "UNCLASSIFIED"] ?? d.spend_category
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="font-semibold">{label}</p>
      <p>{formatCurrency(d.amount)} — {d.percentage.toFixed(1)}%</p>
      <p className="text-muted-foreground">{d.txn_count} transactions</p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  dateRange?: DateRange
  className?: string
}

export function SpendingBreakdownChart({ dateRange = {}, className }: Props) {
  const { data, isLoading } = useSpendCategoryBreakdown(dateRange)

  return (
    <Card className={cn(className)}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Spending Breakdown</CardTitle>
        <p className="text-xs text-muted-foreground">Needs · Wants · Investments</p>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex flex-col items-center gap-4">
            <Skeleton className="h-48 w-48 rounded-full" />
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 w-full">
              {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-4" />)}
            </div>
          </div>
        ) : !data || data.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            No classified spending data yet.
            <br />
            Run the pipeline to classify transactions.
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {/* Donut chart — taller, centered */}
            <div className="h-52 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={data}
                    dataKey="amount"
                    nameKey="spend_category"
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={96}
                    paddingAngle={2}
                    strokeWidth={0}
                  >
                    {data.map((entry) => (
                      <Cell
                        key={entry.spend_category}
                        fill={CATEGORY_COLORS[entry.spend_category as SpendCategory | "UNCLASSIFIED"] ?? "#6b7280"}
                      />
                    ))}
                  </Pie>
                  <Tooltip content={<SpendTooltip />} />
                </PieChart>
              </ResponsiveContainer>
            </div>

            {/* Legend — 2-column grid so it doesn't wrap awkwardly */}
            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
              {data.map((entry) => {
                const key = entry.spend_category as SpendCategory | "UNCLASSIFIED"
                const color = CATEGORY_COLORS[key] ?? "#6b7280"
                const label = CATEGORY_LABELS[key] ?? entry.spend_category
                return (
                  <div key={entry.spend_category} className="flex items-center gap-2 text-xs min-w-0">
                    <span
                      className="size-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                    <span className="truncate text-muted-foreground">{label}</span>
                    <span className="ml-auto shrink-0 font-medium tabular-nums">
                      {entry.percentage.toFixed(0)}%
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
