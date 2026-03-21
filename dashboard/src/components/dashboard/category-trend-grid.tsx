"use client"

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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCategoryTrend } from "@/hooks/use-metrics"
import { CATEGORY_SERIES_COLOR, CHART_GOAL_LINE } from "@/lib/chart-colors"
import { categoryChartKey } from "@/lib/chart-keys"
import { formatCurrency, formatInrChartAxis } from "@/lib/utils"
import type {
  BarDrilldownChart,
  DashboardCategorySeries,
  Goal,
} from "@/lib/types"

const miniAxisX = { fontSize: 9, fill: "var(--muted-foreground)" }
const miniAxisY = { fontSize: 9, fill: "var(--muted-foreground)" }

const SERIES: { id: DashboardCategorySeries; title: string }[] = [
  { id: "swiggy_instamart", title: "Swiggy Instamart" },
  { id: "swiggy_food", title: "Swiggy Food" },
  {
    id: "food_and_dining",
    title: "Food & dining + Swiggy Dineout",
  },
  { id: "shopping", title: "Shopping & e‑commerce" },
  { id: "transport", title: "Transport & fuel" },
  { id: "travel", title: "Travel & stay" },
]

function MiniCategoryChart({
  title,
  series,
  months,
  goalLine,
  setGoalHref,
  onBarClick,
}: {
  title: string
  series: DashboardCategorySeries
  months: number
  goalLine?: number | null
  setGoalHref?: string
  onBarClick: (month: string) => void
}) {
  const { data, isLoading } = useCategoryTrend(series, months)

  if (isLoading) {
    return (
      <Card className="overflow-hidden">
        <CardHeader className="py-3 px-3">
          <CardTitle className="text-xs font-medium leading-tight">{title}</CardTitle>
        </CardHeader>
        <CardContent className="px-2 pb-2 pt-0">
          <Skeleton className="h-[140px] w-full" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="py-3 px-3">
        <div className="flex items-start justify-between gap-1">
          <CardTitle className="text-xs font-medium leading-tight">{title}</CardTitle>
          {setGoalHref && (
            <Link
              href={setGoalHref}
              className="shrink-0 text-[10px] font-medium text-primary underline-offset-2 hover:underline"
            >
              Goal
            </Link>
          )}
        </div>
      </CardHeader>
      {/* px-2 + positive left margin: room for Y ticks (negative margin was clipping "12k" etc.) */}
      <CardContent className="h-[148px] w-full px-2 pb-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data ?? []}
            margin={{ top: 4, right: 2, left: 4, bottom: 2 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="month" tick={miniAxisX} interval="preserveStartEnd" />
            <YAxis
              tick={miniAxisY}
              width={52}
              tickFormatter={(v) => formatInrChartAxis(Number(v))}
            />
            {/* cursor={false}: no default hover band behind bars */}
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
            {goalLine != null && goalLine > 0 && (
              <ReferenceLine
                y={goalLine}
                stroke={CHART_GOAL_LINE}
                strokeDasharray="3 3"
              />
            )}
            <Bar
              dataKey="amount"
              fill={CATEGORY_SERIES_COLOR[series]}
              radius={[2, 2, 0, 0]}
              onClick={(e: { payload?: { month?: string } }) => {
                const m = e?.payload?.month
                if (m)
                  onBarClick(m)
              }}
              cursor="pointer"
            />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}

export function CategoryTrendGrid({
  months,
  goals,
  onBarClick,
}: {
  months: number
  goals?: Goal[] | undefined
  onBarClick: (payload: {
    chart: BarDrilldownChart
    month: string
    series: DashboardCategorySeries
  }) => void
}) {
  return (
    <div>
      <h2 className="text-lg font-semibold mb-2">Category trends</h2>
      <p className="text-sm text-muted-foreground mb-4">
        Six-month view (uses the same window as the toggles above). Click a bar for details.
        In the classifier, only <strong className="text-foreground">Swiggy Instamart</strong> is
        a need; Swiggy Food, Swiggy Dineout, and Food &amp; Dining are wants (ambiguous
        &quot;Swiggy&quot; without a sub-brand is also a want).
      </p>
      {/* 6 tiles → 2×3 on md+ (gifts chart removed so the grid stays even) */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {SERIES.map((s) => {
          const ck = categoryChartKey(s.id)
          const g = goals?.find((x) => x.chart_key === ck)
          const monthlyGoal =
            g && (g.progress_cadence ?? "MONTHLY") === "MONTHLY"
              ? g
              : undefined
          return (
            <MiniCategoryChart
              key={s.id}
              title={s.title}
              series={s.id}
              months={months}
              goalLine={monthlyGoal?.target_amount ?? null}
              setGoalHref={`/goals?chart_key=${encodeURIComponent(ck)}`}
              onBarClick={(month) =>
                onBarClick({ chart: "category", month, series: s.id })
              }
            />
          )
        })}
      </div>
    </div>
  )
}
