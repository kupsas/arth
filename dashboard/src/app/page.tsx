/**
 * Dashboard — redesigned (V2): "This month so far" + "Trends" with drill-down sheets.
 */

"use client"

import * as React from "react"
import Link from "next/link"

import {
  BarDrilldownSheet,
  drilldownTitle,
  type DrilldownParams,
} from "@/components/dashboard/bar-drilldown-sheet"
import { CategoryTrendGrid } from "@/components/dashboard/category-trend-grid"
import { ExpenseStackedBar } from "@/components/dashboard/expense-stacked-bar"
import { GoalProgressSection } from "@/components/dashboard/goal-progress-section"
import { InvestmentTrendChart } from "@/components/dashboard/investment-trend-chart"
import { RemindersCard } from "@/components/dashboard/reminders-card"
import { TimeRangeToggle, type TrendMonths } from "@/components/dashboard/time-range-toggle"
import { TopExpensesCard } from "@/components/dashboard/top-expenses-card"
import { ReviewQueueBanner } from "@/components/review/review-queue-banner"
import { TransactionEditSheet } from "@/components/transactions/transaction-edit-sheet"
import { useGoals } from "@/hooks/use-goals"
import {
  CHART_KEY_EXPENSE_NEED_WANT_STACK,
  CHART_KEY_INVESTMENT_NET,
} from "@/lib/chart-keys"
import type { BarDrilldownChart, DashboardCategorySeries } from "@/lib/types"

export default function DashboardPage() {
  const [trendMonths, setTrendMonths] = React.useState<TrendMonths>(6)
  const [drill, setDrill] = React.useState<DrilldownParams>(null)
  const [topTxnId, setTopTxnId] = React.useState<number | null>(null)

  const { data: goals } = useGoals()

  const investmentGoal =
    goals?.find((g) => g.chart_key === CHART_KEY_INVESTMENT_NET) ??
    goals?.find((g) => g.goal_type === "INVESTMENT")
  const expenseStackGoal =
    goals?.find((g) => g.chart_key === CHART_KEY_EXPENSE_NEED_WANT_STACK) ??
    goals?.find((g) => g.goal_type === "EXPENSE_LIMIT" && !g.linked_category)

  const investmentTarget = investmentGoal?.target_amount ?? null
  /** Monthly charts: hide line for ANNUAL caps (bars are per-month; annual target is misleading). */
  const expenseCapTarget =
    expenseStackGoal &&
    (expenseStackGoal.progress_cadence ?? "MONTHLY") === "MONTHLY"
      ? expenseStackGoal.target_amount ?? null
      : null

  function openDrilldown(p: {
    chart: BarDrilldownChart
    month: string
    series?: DashboardCategorySeries
  }) {
    setDrill(p)
  }

  return (
    <div className="flex flex-col gap-8">
      <ReviewQueueBanner />

      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Home</h1>
        <p className="text-sm text-muted-foreground mt-1">
          This month&apos;s goals and multi-month trends. Upload statements in{" "}
          <Link href="/settings" className="underline underline-offset-2">
            Settings
          </Link>
          .
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">This month so far</h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <GoalProgressSection goals={goals} />
          <div className="flex flex-col gap-4">
            <TopExpensesCard onSelectTransaction={(t) => setTopTxnId(t.id)} />
            <RemindersCard />
          </div>
        </div>
      </section>

      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Trends</h2>
          <TimeRangeToggle value={trendMonths} onChange={setTrendMonths} />
        </div>
        <InvestmentTrendChart
          months={trendMonths}
          goalLine={investmentTarget}
          setGoalHref={`/goals?chart_key=${encodeURIComponent(CHART_KEY_INVESTMENT_NET)}`}
          onBarClick={openDrilldown}
        />
        <ExpenseStackedBar
          months={trendMonths}
          goalLine={expenseCapTarget}
          setGoalHref={`/goals?chart_key=${encodeURIComponent(CHART_KEY_EXPENSE_NEED_WANT_STACK)}`}
          onBarClick={openDrilldown}
        />
        <CategoryTrendGrid
          months={trendMonths}
          goals={goals}
          onBarClick={openDrilldown}
        />
      </section>

      <BarDrilldownSheet
        open={drill != null}
        onOpenChange={(o) => {
          if (!o) setDrill(null)
        }}
        title={drill ? drilldownTitle(drill) : ""}
        params={drill}
      />

      <TransactionEditSheet
        txnId={topTxnId}
        open={topTxnId != null}
        onOpenChange={(o) => {
          if (!o) setTopTxnId(null)
        }}
      />
    </div>
  )
}
