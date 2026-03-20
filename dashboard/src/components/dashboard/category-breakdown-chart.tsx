/**
 * CategoryBreakdownChart — horizontal bar chart showing top outflow (or inflow)
 * categories, ranked by total amount.
 *
 * Design choices:
 *   - Horizontal (layout="vertical" in Recharts' confusing naming) because
 *     category names are long — they'd be unreadable on a vertical bar chart.
 *   - Each bar gets a stable colour from categoryHexColor() so the same
 *     category is always the same colour regardless of its rank.
 *   - Capped at 8 categories to avoid a cramped chart.
 *   - Tooltip shows amount (formatted currency) + share of total (%).
 *   - Toggle between OUTFLOW and INFLOW via tab buttons.
 *
 * Data source: useCategoryBreakdown(dateRange, direction)
 */

"use client"

import * as React from "react"
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Cell,
  Tooltip,
  ResponsiveContainer,
} from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCategoryBreakdown } from "@/hooks/use-metrics"
import {
  categoryHexColor,
  formatCurrency,
  formatCurrencyCompact,
  cn,
} from "@/lib/utils"
import type { DateRange, Direction } from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// Custom tooltip
// ─────────────────────────────────────────────────────────────────────────────

interface TooltipPayloadItem {
  payload?: {
    category: string | null
    amount: number
    percentage: number
    txn_count: number
  }
}

function CategoryTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: TooltipPayloadItem[]
}) {
  if (!active || !payload?.length) return null
  const item = payload[0].payload
  if (!item) return null

  return (
    <div className="rounded-lg border border-border/50 bg-background px-3 py-2 text-xs shadow-xl">
      <p className="mb-1 font-medium">{item.category ?? "Unclassified"}</p>
      <p className="text-muted-foreground">
        {formatCurrency(item.amount)}
        <span className="ml-2 text-foreground font-medium">{item.percentage.toFixed(1)}%</span>
      </p>
      <p className="text-muted-foreground">{item.txn_count} transactions</p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

interface CategoryBreakdownChartProps {
  dateRange: DateRange
  className?: string
}

export function CategoryBreakdownChart({
  dateRange,
  className,
}: CategoryBreakdownChartProps) {
  const [direction, setDirection] = React.useState<Direction>("OUTFLOW")

  const { data, isLoading, isError } = useCategoryBreakdown(dateRange, direction)

  // Take the top 8 categories — beyond that the bars get unreadably thin
  const chartData = (data ?? []).slice(0, 8).map((cat) => ({
    ...cat,
    category: cat.category ?? "Unclassified",
    // Truncate very long names for the Y-axis
    label: (cat.category ?? "Unclassified").length > 22
      ? (cat.category ?? "Unclassified").slice(0, 21) + "…"
      : (cat.category ?? "Unclassified"),
  }))

  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">Outflow by Category</CardTitle>
          {/* Direction toggle */}
          <div className="flex items-center rounded-md border border-border p-0.5">
            {(["OUTFLOW", "INFLOW"] as Direction[]).map((dir) => (
              <button
                key={dir}
                onClick={() => setDirection(dir)}
                className={cn(
                  "rounded px-2 py-0.5 text-xs font-medium transition-colors",
                  direction === dir
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {dir === "OUTFLOW" ? "Outflow" : "Inflow"}
              </button>
            ))}
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex-1">
        {isLoading && (
          <div className="flex flex-col gap-3 pt-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" style={{ width: `${90 - i * 10}%` }} />
            ))}
          </div>
        )}

        {isError && (
          <p className="pt-4 text-center text-sm text-muted-foreground">
            Failed to load category data.
          </p>
        )}

        {!isLoading && !isError && chartData.length === 0 && (
          <p className="pt-4 text-center text-sm text-muted-foreground">
            No transactions in this period.
          </p>
        )}

        {!isLoading && !isError && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart
              data={chartData}
              layout="vertical"
              margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
            >
              {/* X axis: amount values */}
              <XAxis
                type="number"
                tickFormatter={formatCurrencyCompact}
                // Theme vars in globals.css are `oklch(...)`, so do NOT wrap in `hsl()`.
                // Use CSS variables directly so dark/light work correctly.
                tick={{ fontSize: 11, fill: "var(--foreground)" }}
                axisLine={false}
                tickLine={false}
              />
              {/* Y axis: category names (truncated) */}
              <YAxis
                type="category"
                dataKey="label"
                width={150}
                tick={{ fontSize: 11, fill: "var(--foreground)" }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                content={<CategoryTooltip />}
                cursor={{ fill: "var(--muted)", opacity: 0.5 }}
              />
              <Bar dataKey="amount" radius={[0, 4, 4, 0]} maxBarSize={28}>
                {chartData.map((cat) => (
                  <Cell
                    key={cat.category}
                    fill={categoryHexColor(cat.category)}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}
