"use client"

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { RECHARTS_TOOLTIP_CARD_CLASS } from "@/components/dashboard/recharts-tooltip"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useInvestmentTrend } from "@/hooks/use-metrics"
import {
  CHART_GOAL_LINE,
  CHART_PURCHASE,
  CHART_SALE,
} from "@/lib/chart-colors"
import { investmentNetYAxisDomain } from "@/lib/chart-axis"
import { formatCurrency, formatInrChartAxis } from "@/lib/utils"
import type { BarDrilldownChart, InvestmentTrendRow } from "@/lib/types"
import Link from "next/link"

const axisTick = { fontSize: 11, fill: "var(--muted-foreground)" }

interface InvestmentTrendChartProps {
  months: number
  /** Target monthly net investment (purchases − sales). */
  goalLine?: number | null
  setGoalHref?: string
  onBarClick: (payload: {
    chart: BarDrilldownChart
    month: string
  }) => void
}

function InvestmentNetTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean
  /** Recharts Tooltip passes `label` as string | number depending on axis. */
  label?: string | number
  payload?: readonly { payload?: InvestmentTrendRow }[]
}) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload
  if (!row) return null

  return (
    <div className={RECHARTS_TOOLTIP_CARD_CLASS}>
      <p className="font-medium leading-tight">
        Month {label != null ? String(label) : ""}
      </p>
      <ul className="mt-1.5 space-y-1 tabular-nums text-muted-foreground">
        <li className="flex justify-between gap-6">
          <span>Net</span>
          <span>{formatCurrency(row.net)}</span>
        </li>
        <li className="flex justify-between gap-6">
          <span>Purchases</span>
          <span>{formatCurrency(row.purchases)}</span>
        </li>
        <li className="flex justify-between gap-6">
          <span>Sales</span>
          <span>{formatCurrency(row.sales)}</span>
        </li>
      </ul>
    </div>
  )
}

export function InvestmentTrendChart({
  months,
  goalLine,
  setGoalHref,
  onBarClick,
}: InvestmentTrendChartProps) {
  const { data, isLoading } = useInvestmentTrend(months)

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Investments</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[280px] w-full" />
        </CardContent>
      </Card>
    )
  }

  const chartData = data ?? []
  const [invYMin, invYMax] = investmentNetYAxisDomain(chartData)

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <CardTitle className="text-base">Investments</CardTitle>
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
          Net flow per month (purchases − sales). Hover a bar for gross purchases and sales. Click
          for all investment transactions that month.
        </p>
      </CardHeader>
      <CardContent className="h-[300px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="month" tick={axisTick} />
            <YAxis
              domain={[invYMin, invYMax]}
              tick={axisTick}
              tickFormatter={(v) => formatInrChartAxis(Number(v))}
            />
            <Tooltip
              cursor={false}
              content={(props) => <InvestmentNetTooltip {...props} />}
            />
            <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="2 2" />
            {goalLine != null && goalLine > 0 && (
              <ReferenceLine
                y={goalLine}
                stroke={CHART_GOAL_LINE}
                strokeDasharray="4 4"
              />
            )}
            <Bar
              dataKey="net"
              name="Net investment"
              radius={[4, 4, 4, 4]}
              onClick={(e: { payload?: InvestmentTrendRow }) => {
                const m = e?.payload?.month
                if (m) onBarClick({ chart: "investment_month", month: m })
              }}
              cursor="pointer"
            >
              {chartData.map((entry) => (
                <Cell
                  key={entry.month}
                  fill={entry.net >= 0 ? CHART_PURCHASE : CHART_SALE}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
