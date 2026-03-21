/**
 * Dashboard — redesigned (V2): "This month so far" + "Trends" with drill-down sheets.
 */

"use client"

import * as React from "react"
import Link from "next/link"
import { AlertTriangle } from "lucide-react"

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
import { TransactionEditSheet } from "@/components/transactions/transaction-edit-sheet"
import { useGoals } from "@/hooks/use-goals"
import { useTransactions } from "@/hooks/use-transactions"
import type { BarDrilldownChart, DashboardCategorySeries } from "@/lib/types"

export default function DashboardPage() {
  const [trendMonths, setTrendMonths] = React.useState<TrendMonths>(6)
  const [drill, setDrill] = React.useState<DrilldownParams>(null)
  const [topTxnId, setTopTxnId] = React.useState<number | null>(null)

  const { data: goals } = useGoals()
  const investmentGoalId =
    goals?.find((g) => g.goal_type === "INVESTMENT")?.id ?? null
  const expenseGoalId =
    goals?.find((g) => g.goal_type === "EXPENSE_LIMIT" && !g.linked_category)?.id ??
    null
  const investmentTarget =
    goals?.find((g) => g.goal_type === "INVESTMENT")?.target_amount ?? null

  const { data: unreviewedData } = useTransactions({
    is_reviewed: false,
    page: 1,
    page_size: 1,
  })
  const unreviewedCount = unreviewedData?.total ?? 0

  function openDrilldown(p: {
    chart: BarDrilldownChart
    month: string
    series?: DashboardCategorySeries
  }) {
    setDrill(p)
  }

  return (
    <div className="flex flex-col gap-8">
      {unreviewedCount > 0 && (
        <Link
          href="/review"
          className="flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm transition-colors hover:bg-amber-500/20"
        >
          <AlertTriangle className="size-4 shrink-0 text-amber-500" />
          <span className="flex-1 text-amber-700 dark:text-amber-400">
            <strong>{unreviewedCount.toLocaleString()} transaction{unreviewedCount !== 1 ? "s" : ""}</strong> need
            {unreviewedCount === 1 ? "s" : ""} review.
          </span>
          <span className="shrink-0 text-xs font-medium text-amber-600 dark:text-amber-400 underline underline-offset-2">
            Review Queue →
          </span>
        </Link>
      )}

      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
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
          <GoalProgressSection
            investmentGoalId={investmentGoalId}
            expenseGoalId={expenseGoalId}
          />
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
          onBarClick={openDrilldown}
        />
        <ExpenseStackedBar months={trendMonths} onBarClick={openDrilldown} />
        <CategoryTrendGrid months={trendMonths} onBarClick={openDrilldown} />
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
