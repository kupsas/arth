"use client"

import * as React from "react"

import { TransactionEditSheet } from "@/components/transactions/transaction-edit-sheet"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { useBarDrilldown } from "@/hooks/use-metrics"
import { cn, formatCurrency, formatDate } from "@/lib/utils"
import type { BarDrilldownChart, DashboardCategorySeries, Transaction } from "@/lib/types"

export type DrilldownParams = {
  chart: BarDrilldownChart
  month: string
  series?: DashboardCategorySeries
} | null

function isInvestmentDrilldown(chart: BarDrilldownChart | undefined): boolean {
  return (
    chart === "investment_month" ||
    chart === "investment_purchase" ||
    chart === "investment_sale"
  )
}

/** Investment sheet: outflow = purchases (green, matches net chart). Inflow = sales (red). Else: default text. */
function drilldownAmountTextClass(
  direction: Transaction["direction"],
  investmentSheet: boolean,
): string {
  if (!investmentSheet) return "text-foreground"
  if (direction === "OUTFLOW") return "text-emerald-600 dark:text-emerald-400"
  if (direction === "INFLOW") return "text-rose-600 dark:text-rose-400"
  return "text-foreground"
}

interface BarDrilldownSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  params: DrilldownParams
}

export function BarDrilldownSheet({
  open,
  onOpenChange,
  title,
  params,
}: BarDrilldownSheetProps) {
  const { data, isLoading } = useBarDrilldown(params)
  const [editId, setEditId] = React.useState<number | null>(null)

  React.useEffect(() => {
    if (!open) setEditId(null)
  }, [open])

  const investmentSheet = isInvestmentDrilldown(params?.chart ?? undefined)

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{title}</SheetTitle>
            <SheetDescription>
              Transactions included in this bar. Open one to edit or exclude from analytics.
            </SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-4">
            {isLoading && (
              <div className="space-y-2 mt-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            )}
            {!isLoading && (!data || data.length === 0) && (
              <p className="text-sm text-muted-foreground mt-4">No transactions in this bucket.</p>
            )}
            {data && data.length > 0 && (
              <ul className="divide-y divide-border rounded-md border border-border mt-4">
                {data.map((txn: Transaction) => (
                  <li key={txn.id}>
                    <button
                      type="button"
                      onClick={() => setEditId(txn.id)}
                      className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
                    >
                      <span className="min-w-0 truncate">
                        <span className="text-muted-foreground text-xs">
                          {formatDate(txn.txn_date)}
                        </span>{" "}
                        <span
                          className={cn(
                            "font-medium",
                            drilldownAmountTextClass(txn.direction, investmentSheet),
                          )}
                        >
                          {txn.counterparty || txn.raw_description}
                        </span>
                      </span>
                      <span
                        className={cn(
                          "shrink-0 font-mono tabular-nums",
                          drilldownAmountTextClass(txn.direction, investmentSheet),
                        )}
                      >
                        {formatCurrency(txn.amount)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <TransactionEditSheet
        txnId={editId}
        open={editId != null}
        onOpenChange={(o) => {
          if (!o) setEditId(null)
        }}
        nested
        onNestedBack={() => setEditId(null)}
      />
    </>
  )
}

/** Build a human-readable title for the drill-down sheet. */
export function drilldownTitle(p: NonNullable<DrilldownParams>): string {
  const m = p.month
  switch (p.chart) {
    case "investment_purchase":
      return `Investment purchases · ${m}`
    case "investment_sale":
      return `Investment sales · ${m}`
    case "expense_need":
      return `Need spend · ${m}`
    case "expense_want":
      return `Want spend · ${m}`
    case "investment_month":
      return `Investment activity · ${m}`
    case "category":
      return `Category · ${p.series ?? "?"} · ${m}`
    default:
      return m
  }
}
