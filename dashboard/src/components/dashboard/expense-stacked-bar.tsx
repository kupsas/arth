"use client"

import * as React from "react"
import Link from "next/link"
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
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
import {
  CHART_GOAL_LINE,
  CHART_NEED,
  CHART_WANT,
} from "@/lib/chart-colors"
import { expenseStackedYAxisMax } from "@/lib/chart-axis"
import { formatCurrency, formatInrChartAxis } from "@/lib/utils"
import type { BarDrilldownChart } from "@/lib/types"

const axisTick = { fontSize: 11, fill: "var(--muted-foreground)" }

interface ExpenseStackedBarProps {
  months: number
  /** Monthly cap in INR (same basis as needs+wants chart). Hidden in 100% mode. */
  goalLine?: number | null
  /** Link to Goals with chart_key pre-selected (e.g. /goals?chart_key=expense_need_want_stack). */
  setGoalHref?: string
  onBarClick: (payload: { chart: BarDrilldownChart; month: string }) => void
}

/**
 * Needs vs wants per month (stacked bars), optional Amount vs 100% view, optional monthly cap line.
 */
export function ExpenseStackedBar({
  months,
  goalLine,
  setGoalHref,
  onBarClick,
}: ExpenseStackedBarProps) {
  const { data, isLoading } = useExpenseTrendStacked(months)
  const [pctMode, setPctMode] = React.useState(false)

  const chartData = React.useMemo(() => {
    if (!data) return []
    const base = data.map((row) => {
      const needRaw = Number(row.need)
      const wantRaw = Number(row.want)
      const need = Number.isFinite(needRaw) ? Math.max(0, needRaw) : 0
      const want = Number.isFinite(wantRaw) ? Math.max(0, wantRaw) : 0
      return { month: row.month, need, want }
    })
    if (!pctMode) return base
    return base.map((row) => {
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

  const showGoalLine =
    goalLine != null && goalLine > 0 && !pctMode

  // Data-driven ceiling (mini-charts style). Avoids goal-line extendDomain pinning axis to 6L etc.
  const amountYMax = expenseStackedYAxisMax(chartData)

  return (
    <Card className="overflow-visible">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 space-y-0">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <CardTitle className="text-base">Expenses — needs vs wants</CardTitle>
            {setGoalHref && (
              <Link
                href={setGoalHref}
                className="text-xs font-medium text-primary underline-offset-2 hover:underline"
              >
                Set goal
              </Link>
            )}
          </div>
          <p className="text-xs text-muted-foreground font-normal mt-1">
            Needs and wants per month (stacked). Click a segment to list transactions for that month
            and bucket.
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
      {/*
        Avoid Recharts <Legend /> with two <Bar /> (layout bug in Recharts 2.15 + React 19).
        HTML legend above the chart instead.
      */}
      <CardContent className="flex h-[340px] w-full min-h-[340px] shrink-0 flex-col overflow-visible pt-0">
        <div className="flex shrink-0 justify-end gap-4 pb-1 pt-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: CHART_NEED }}
              aria-hidden
            />
            Needs
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: CHART_WANT }}
              aria-hidden
            />
            Wants
          </span>
        </div>
        <div className="h-[308px] min-w-0 w-full shrink-0">
          <ResponsiveContainer width="100%" height={308}>
            <BarChart
              key={`expense-${pctMode ? "pct" : "amt"}`}
              data={chartData}
              margin={{ top: 8, right: 12, left: 4, bottom: 0 }}
              barCategoryGap="12%"
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="month" tick={axisTick} />
              <YAxis
                width={56}
                {...(pctMode
                  ? { domain: [0, 100] as const }
                  : { domain: [0, amountYMax] as const })}
                tick={axisTick}
                tickFormatter={(v) =>
                  pctMode ? `${v}%` : formatInrChartAxis(Number(v))
                }
              />
              <Tooltip
                cursor={false}
                content={(props) => (
                  <RechartsTooltipCard
                    {...props}
                    labelPrefix="Month "
                    showTotal
                    formatValue={(v) => {
                      if (typeof v !== "number") return String(v)
                      return pctMode ? `${v}%` : formatCurrency(v)
                    }}
                  />
                )}
              />
              {showGoalLine && (
                <ReferenceLine
                  y={goalLine}
                  stroke={CHART_GOAL_LINE}
                  strokeDasharray="4 4"
                />
              )}
              <Bar
                dataKey="need"
                name="Needs"
                stackId="needWant"
                fill={CHART_NEED}
                isAnimationActive={false}
                minPointSize={2}
                radius={[0, 0, 0, 0]}
                onClick={(e: { payload?: { month?: string } }) => {
                  const m = e?.payload?.month
                  if (m) onBarClick({ chart: "expense_need", month: m })
                }}
                cursor="pointer"
              />
              <Bar
                dataKey="want"
                name="Wants"
                stackId="needWant"
                fill={CHART_WANT}
                isAnimationActive={false}
                minPointSize={2}
                radius={[4, 4, 0, 0]}
                onClick={(e: { payload?: { month?: string } }) => {
                  const m = e?.payload?.month
                  if (m) onBarClick({ chart: "expense_want", month: m })
                }}
                cursor="pointer"
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  )
}
