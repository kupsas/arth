/**
 * MonthlyTrendChart — area chart showing inflow vs outflow over trailing months.
 *
 * Design:
 *   - Two overlapping areas: inflow (emerald) on top, outflow (rose) below
 *   - Gradient fills so the areas are visually distinct without being garish
 *   - X-axis: short month labels ("Mar '25")
 *   - Y-axis: compact currency (₹1.2L, ₹45K)
 *   - Hover tooltip shows inflow, outflow, net, and savings rate
 *   - Month selector: 6 / 12 / 24 months (top-right of the card)
 *
 * Note: useMonthlyTrend() returns data for ALL trailing N months regardless
 * of the date range picker — the trend chart is always a trailing window.
 * This is intentional: showing a 3-month trend on a 3-month window would
 * collapse to a single bar, which is useless.
 */

"use client"

import * as React from "react"
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useMonthlyTrend } from "@/hooks/use-metrics"
import {
  formatCurrency,
  formatCurrencyCompact,
  formatMonthShort,
  formatPercent,
  cn,
} from "@/lib/utils"

// ─────────────────────────────────────────────────────────────────────────────
// Custom tooltip
// ─────────────────────────────────────────────────────────────────────────────

interface TrendTooltipPayload {
  month: string
  income: number
  expense: number
  net: number
  savings_rate: number
}

function TrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ payload: TrendTooltipPayload }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload

  const netColor = d.net >= 0 ? "text-emerald-500" : "text-rose-500"

  return (
    <div className="rounded-lg border border-border/50 bg-background px-3 py-2.5 text-xs shadow-xl">
      <p className="mb-2 font-medium">{label}</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1">
        <span className="text-muted-foreground">Inflow</span>
        <span className="text-right font-medium text-emerald-500 tabular-nums">
          {formatCurrency(d.income)}
        </span>
        <span className="text-muted-foreground">Outflow</span>
        <span className="text-right font-medium text-rose-500 tabular-nums">
          {formatCurrency(d.expense)}
        </span>
        <span className="text-muted-foreground">Net</span>
        <span className={cn("text-right font-medium tabular-nums", netColor)}>
          {formatCurrency(d.net)}
        </span>
        <span className="text-muted-foreground">Savings Rate</span>
        <span className="text-right font-medium tabular-nums">
          {formatPercent(d.savings_rate)}
        </span>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

const MONTH_OPTIONS = [6, 12, 24] as const
type MonthWindow = (typeof MONTH_OPTIONS)[number]

interface MonthlyTrendChartProps {
  className?: string
}

export function MonthlyTrendChart({ className }: MonthlyTrendChartProps) {
  const [months, setMonths] = React.useState<MonthWindow>(12)

  const { data, isLoading, isError } = useMonthlyTrend(months)

  // Format data for Recharts: convert "YYYY-MM" to a readable tick label
  const chartData = (data ?? []).map((row) => ({
    ...row,
    label: formatMonthShort(row.month),
  }))

  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-4">
            <CardTitle className="text-base">Monthly Trend</CardTitle>
            {/* Legend */}
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-3 rounded-sm bg-emerald-500" />
                Inflow
              </span>
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-3 rounded-sm bg-rose-500" />
                Outflow
              </span>
            </div>
          </div>
          {/* Month window selector */}
          <div className="flex items-center rounded-md border border-border p-0.5">
            {MONTH_OPTIONS.map((m) => (
              <button
                key={m}
                onClick={() => setMonths(m)}
                className={cn(
                  "rounded px-2 py-0.5 text-xs font-medium transition-colors",
                  months === m
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {m}M
              </button>
            ))}
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex-1">
        {isLoading && (
          <div className="flex items-end gap-1 pt-4" style={{ height: 260 }}>
            {/* Fixed heights give a plausible bar-chart silhouette without Math.random() */}
            {[55, 70, 45, 80, 60, 75, 50, 65, 85, 55, 70, 40].slice(0, months).map((h, i) => (
              <Skeleton
                key={i}
                className="flex-1"
                style={{ height: `${h}%` }}
              />
            ))}
          </div>
        )}

        {isError && (
          <p className="pt-4 text-center text-sm text-muted-foreground">
            Failed to load trend data.
          </p>
        )}

        {!isLoading && !isError && chartData.length === 0 && (
          <p className="pt-4 text-center text-sm text-muted-foreground">
            No data yet.
          </p>
        )}

        {!isLoading && !isError && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart
              data={chartData}
              margin={{ top: 8, right: 8, bottom: 0, left: 8 }}
            >
              {/*
               * SVG gradient definitions.
               * We reference these by ID in the Area fill prop.
               */}
              <defs>
                <linearGradient id="incomeGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#10b981" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="expenseGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#f43f5e" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#f43f5e" stopOpacity={0.02} />
                </linearGradient>
              </defs>

              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--border)"
                vertical={false}
              />

              <XAxis
                dataKey="label"
                // Theme vars in globals.css are `oklch(...)`, so do NOT wrap in `hsl()`.
                tick={{ fontSize: 11, fill: "var(--foreground)" }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
              />

              <YAxis
                tickFormatter={formatCurrencyCompact}
                tick={{ fontSize: 11, fill: "var(--foreground)" }}
                axisLine={false}
                tickLine={false}
                width={52}
              />

              <Tooltip
                content={<TrendTooltip />}
                cursor={{ stroke: "var(--border)", strokeWidth: 1 }}
              />

              {/* Inflow area — drawn first so outflow overlaps it if needed */}
              <Area
                type="monotone"
                dataKey="income"
                stroke="#10b981"
                strokeWidth={2}
                fill="url(#incomeGradient)"
                dot={false}
                activeDot={{ r: 4, fill: "#10b981", strokeWidth: 0 }}
              />

              {/* Outflow area */}
              <Area
                type="monotone"
                dataKey="expense"
                stroke="#f43f5e"
                strokeWidth={2}
                fill="url(#expenseGradient)"
                dot={false}
                activeDot={{ r: 4, fill: "#f43f5e", strokeWidth: 0 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}
