"use client"

/**
 * Goals — full-page home for the goals section (moved off the dashboard).
 *
 * Optional query: <code>?chart_key=expense_need_want_stack</code> (etc.) pre-fills
 * the Add Goal sheet when opened from a dashboard chart link.
 */

import * as React from "react"
import { useSearchParams } from "next/navigation"

import { GoalsSection } from "@/components/dashboard/goals-section"
import { Skeleton } from "@/components/ui/skeleton"

function GoalsPageContent() {
  const searchParams = useSearchParams()
  const chartKey = searchParams.get("chart_key")

  return (
    <div className="max-w-4xl">
      <GoalsSection initialChartKey={chartKey} />
    </div>
  )
}

export default function GoalsPage() {
  return (
    <React.Suspense
      fallback={
        <div className="max-w-4xl space-y-3">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-40 w-full" />
        </div>
      }
    >
      <GoalsPageContent />
    </React.Suspense>
  )
}
