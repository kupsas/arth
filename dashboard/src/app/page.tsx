/**
 * Dashboard page — the "wow" screen and the first thing users see.
 *
 * Layout (top → bottom):
 *   1. Date Range Picker (top-right, controls everything below)
 *   2. Summary Cards (4-up grid: Inflow / Outflow / Savings / Savings Rate)
 *   3. Two-column row:
 *        Left  (~60%): Category Breakdown horizontal bar chart
 *        Right (~40%): Top Counterparties table
 *   4. Monthly Trend area chart (full width, always trailing N months)
 *
 * State:
 *   - `preset` — which quick-select is active ("this-month" by default)
 *   - `dateRange` — the DateRange derived from the preset (or custom)
 *   - `previousRange` — the period immediately before dateRange (for MoM deltas)
 *
 * All child components are "use client" so this page is also marked "use client".
 * (There's no server-side data fetching here — everything goes through React Query.)
 */

"use client"

import * as React from "react"
import Link from "next/link"
import { AlertTriangle } from "lucide-react"

import {
  DateRangePicker,
  getPresetRange,
  getPreviousRange,
  type Preset,
} from "@/components/dashboard/date-range-picker"
import { SummaryCards } from "@/components/dashboard/summary-cards"
import { CategoryBreakdownChart } from "@/components/dashboard/category-breakdown-chart"
import { MonthlyTrendChart } from "@/components/dashboard/monthly-trend-chart"
import { TopCounterpartiesTable } from "@/components/dashboard/top-counterparties-table"
import { SpendingBreakdownChart } from "@/components/dashboard/spending-breakdown-chart"
import { RecurringCard } from "@/components/dashboard/recurring-card"
import { GoalsSection } from "@/components/dashboard/goals-section"
import { UploadButton } from "@/components/dashboard/upload-button"
import { useTransactions } from "@/hooks/use-transactions"
import { useNegativeSurplusMonths } from "@/hooks/use-metrics"
import { formatCurrency } from "@/lib/utils"
import type { DateRange } from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  // Lightweight call to check how many transactions are waiting for review.
  // page_size: 1 means we only fetch one row — we only care about the `total` count.
  // Note: metrics endpoints currently reflect ALL transactions (reviewed + unreviewed).
  // Filtering metrics to reviewed-only requires a backend update (Phase 4 candidate).
  const { data: unreviewedData } = useTransactions({
    is_reviewed: false,
    page: 1,
    page_size: 1,
  })
  const unreviewedCount = unreviewedData?.total ?? 0

  // Q11: how many of the last 12 months spent more than earned?
  const { data: deficitData } = useNegativeSurplusMonths(12)

  // Default to "this month" — the most useful view on first load
  const [preset, setPreset] = React.useState<Preset>("this-month")
  const [dateRange, setDateRange] = React.useState<DateRange>(
    getPresetRange("this-month")
  )
  const [previousRange, setPreviousRange] = React.useState<DateRange>(
    getPreviousRange("this-month")
  )
  // Separate state for custom ranges (only used when preset === "custom")
  const [customRange, setCustomRange] = React.useState<DateRange>({})

  function handlePresetChange(newPreset: Preset, newRange: DateRange) {
    setPreset(newPreset)
    setDateRange(newRange)
    setPreviousRange(getPreviousRange(newPreset))
  }

  function handleCustomChange(newRange: DateRange) {
    // For custom ranges we can't easily derive a "previous period" automatically,
    // so we compute a same-duration window shifted back by the same number of days.
    setPreset("custom")
    setCustomRange(newRange)
    setDateRange(newRange)

    if (newRange.date_from && newRange.date_to) {
      const from = new Date(newRange.date_from + "T00:00:00")
      const to   = new Date(newRange.date_to   + "T00:00:00")
      const durationMs = to.getTime() - from.getTime()
      const prevTo   = new Date(from.getTime() - 1)                 // day before range starts
      const prevFrom = new Date(prevTo.getTime() - durationMs)
      setPreviousRange({
        date_from: prevFrom.toISOString().split("T")[0],
        date_to:   prevTo.toISOString().split("T")[0],
      })
    }
  }

  return (
    <div className="flex flex-col gap-6">

      {/* ── Unreviewed transactions banner ────────────────────────── */}
      {unreviewedCount > 0 && (
        <Link
          href="/review"
          className="flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm transition-colors hover:bg-amber-500/20"
        >
          <AlertTriangle className="size-4 shrink-0 text-amber-500" />
          <span className="flex-1 text-amber-700 dark:text-amber-400">
            <strong>{unreviewedCount.toLocaleString()} transaction{unreviewedCount !== 1 ? "s" : ""}</strong> need{unreviewedCount === 1 ? "s" : ""} review.
            {" "}The insights below include unreviewed data — review them to ensure accuracy.
          </span>
          <span className="shrink-0 text-xs font-medium text-amber-600 dark:text-amber-400 underline underline-offset-2">
            Go to Review Queue →
          </span>
        </Link>
      )}

      {/* ── Deficit months callout (Q11) ─────────────────────────── */}
      {deficitData && deficitData.months_with_deficit > 0 && (
        <div className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-500" />
          <div className="flex-1 text-red-700 dark:text-red-400">
            <strong>
              {deficitData.months_with_deficit} of the last{" "}
              {deficitData.total_months} months
            </strong>{" "}
            had outflow that exceeded inflow — totalling a{" "}
            <strong>{formatCurrency(deficitData.total_deficit)}</strong>{" "}
            shortfall.
            {deficitData.deficit_months.length > 0 && (
              <span className="ml-1 text-red-600/80 dark:text-red-400/80">
                (
                {deficitData.deficit_months
                  .map((m) => m.month)
                  .join(", ")}
                )
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Date Range Picker ─────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold">Overview</h1>
          <p className="text-sm text-muted-foreground">
            Your financial snapshot at a glance.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <UploadButton />
          <DateRangePicker
            preset={preset}
            customRange={customRange}
            onPresetChange={handlePresetChange}
            onCustomChange={handleCustomChange}
          />
        </div>
      </div>

      {/* ── Summary Cards ─────────────────────────────────────────── */}
      <SummaryCards
        currentRange={dateRange}
        previousRange={previousRange}
      />

      {/* ── Spending Breakdown (N/W/S/I) + Recurring ─────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <SpendingBreakdownChart dateRange={dateRange} className="lg:col-span-3" />
        <RecurringCard className="lg:col-span-2" />
      </div>

      {/* ── Category Breakdown + Top Counterparties ───────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        {/* Category bar chart takes ~60% of the row */}
        <CategoryBreakdownChart
          dateRange={dateRange}
          className="lg:col-span-3"
        />
        {/* Top counterparties table takes ~40% */}
        <TopCounterpartiesTable
          dateRange={dateRange}
          className="lg:col-span-2"
        />
      </div>

      {/* ── Monthly Trend Chart ───────────────────────────────────── */}
      <MonthlyTrendChart />

      {/* ── Goals ─────────────────────────────────────────────────── */}
      <GoalsSection />

    </div>
  )
}
