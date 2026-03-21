"use client"

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { RechartsTooltipCard } from "@/components/dashboard/recharts-tooltip"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useInvestmentTrend } from "@/hooks/use-metrics"
import {
  CHART_GOAL_LINE,
  CHART_PURCHASE,
  CHART_SALE,
} from "@/lib/chart-colors"
import { formatCurrency, formatInrChartAxis } from "@/lib/utils"
import type { BarDrilldownChart } from "@/lib/types"

const axisTick = { fontSize: 11, fill: "var(--muted-foreground)" }

interface InvestmentTrendChartProps {
  months: number
  goalLine?: number | null
  onBarClick: (payload: {
    chart: BarDrilldownChart
    month: string
  }) => void
}

export function InvestmentTrendChart({
  months,
  goalLine,
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

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Investments</CardTitle>
        <p className="text-xs text-muted-foreground font-normal">
          Gross purchases vs sales by month; click a bar to see transactions. Net = purchases −
          sales.
        </p>
      </CardHeader>
      <CardContent className="h-[300px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="month" tick={axisTick} />
            <YAxis
              tick={axisTick}
              tickFormatter={(v) => formatInrChartAxis(Number(v))}
            />
            {/* cursor={false}: hide Recharts' default full-height hover rectangle */}
            <Tooltip
              cursor={false}
              content={(props) => (
                <RechartsTooltipCard
                  {...props}
                  labelPrefix="Month "
                  formatValue={(v) =>
                    typeof v === "number" ? formatCurrency(v) : String(v)
                  }
                />
              )}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: "var(--foreground)" }} />
            {goalLine != null && goalLine > 0 && (
              <ReferenceLine
                y={goalLine}
                stroke={CHART_GOAL_LINE}
                strokeDasharray="4 4"
              />
            )}
            <Bar
              dataKey="purchases"
              name="Purchases"
              fill={CHART_PURCHASE}
              radius={[4, 4, 0, 0]}
              onClick={(e: { payload?: { month?: string } }) => {
                const m = e?.payload?.month
                if (m) onBarClick({ chart: "investment_purchase", month: m })
              }}
              cursor="pointer"
            />
            <Bar
              dataKey="sales"
              name="Sales (proceeds)"
              fill={CHART_SALE}
              radius={[4, 4, 0, 0]}
              onClick={(e: { payload?: { month?: string } }) => {
                const m = e?.payload?.month
                if (m) onBarClick({ chart: "investment_sale", month: m })
              }}
              cursor="pointer"
            />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
