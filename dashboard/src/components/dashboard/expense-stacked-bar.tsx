"use client"

import * as React from "react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { RechartsTooltipCard } from "@/components/dashboard/recharts-tooltip"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useExpenseTrendStacked } from "@/hooks/use-metrics"
import { CHART_NEED, CHART_WANT } from "@/lib/chart-colors"
import { formatCurrency, formatInrChartAxis } from "@/lib/utils"
import type { BarDrilldownChart } from "@/lib/types"

const axisTick = { fontSize: 11, fill: "var(--muted-foreground)" }

interface ExpenseStackedBarProps {
  months: number
  onBarClick: (payload: { chart: BarDrilldownChart; month: string }) => void
}

/**
 * Hero chart: needs vs wants per month, toggle absolute vs 100% stacked.
 */
export function ExpenseStackedBar({ months, onBarClick }: ExpenseStackedBarProps) {
  const { data, isLoading } = useExpenseTrendStacked(months)
  const [pctMode, setPctMode] = React.useState(false)

  const chartData = React.useMemo(() => {
    if (!data) return []
    if (!pctMode) return data
    return data.map((row) => {
      const t = row.need + row.want
      if (t <= 0) return { ...row, need: 0, want: 0 }
      return {
        month: row.month,
        need: Math.round((row.need / t) * 1000) / 10,
        want: Math.round((row.want / t) * 1000) / 10,
      }
    })
  }, [data, pctMode])

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Expenses — needs vs wants</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[320px] w-full" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 space-y-0">
        <div>
          <CardTitle className="text-base">Expenses — needs vs wants</CardTitle>
          <p className="text-xs text-muted-foreground font-normal mt-1">
            Click a segment to list transactions for that month and bucket.
          </p>
        </div>
        <div className="flex gap-1">
          <Button
            type="button"
            size="sm"
            variant={!pctMode ? "default" : "outline"}
            className="h-7 text-xs"
            onClick={() => setPctMode(false)}
          >
            Amount
          </Button>
          <Button
            type="button"
            size="sm"
            variant={pctMode ? "default" : "outline"}
            className="h-7 text-xs"
            onClick={() => setPctMode(true)}
          >
            100%
          </Button>
        </div>
      </CardHeader>
      <CardContent className="h-[340px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="month" tick={axisTick} />
            <YAxis
              tick={axisTick}
              tickFormatter={(v) =>
                pctMode ? `${v}%` : formatInrChartAxis(Number(v))
              }
            />
            {/* cursor={false}: no default hover band behind bars */}
            <Tooltip
              cursor={false}
              content={(props) => (
                <RechartsTooltipCard
                  {...props}
                  labelPrefix="Month "
                  formatValue={(v) => {
                    if (typeof v !== "number") return String(v)
                    return pctMode ? `${v}%` : formatCurrency(v)
                  }}
                />
              )}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: "var(--foreground)" }} />
            <Bar
              dataKey="need"
              name="Needs"
              stackId="a"
              fill={CHART_NEED}
              onClick={(e: { payload?: { month?: string } }) => {
                const m = e?.payload?.month
                if (m) onBarClick({ chart: "expense_need", month: m })
              }}
              cursor="pointer"
            />
            <Bar
              dataKey="want"
              name="Wants"
              stackId="a"
              fill={CHART_WANT}
              onClick={(e: { payload?: { month?: string } }) => {
                const m = e?.payload?.month
                if (m) onBarClick({ chart: "expense_want", month: m })
              }}
              cursor="pointer"
            />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
