"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useTopExpenses } from "@/hooks/use-metrics"
import { formatCurrency, formatDate } from "@/lib/utils"
import type { Transaction } from "@/lib/types"

interface TopExpensesCardProps {
  onSelectTransaction: (txn: Transaction) => void
}

/** All outflows ≥ ₹5,000 in the current calendar month. */
export function TopExpensesCard({ onSelectTransaction }: TopExpensesCardProps) {
  const { data, isLoading } = useTopExpenses(5000)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Large expenses this month</CardTitle>
        <p className="text-xs text-muted-foreground font-normal">
          Transactions ≥ ₹5,000 (click to edit).
        </p>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        )}
        {!isLoading && (!data || data.length === 0) && (
          <p className="text-sm text-muted-foreground">No transactions above ₹5,000 this month.</p>
        )}
        {data && data.length > 0 && (
          <ul className="divide-y divide-border rounded-md border border-border">
            {data.map((txn) => (
              <li key={txn.id}>
                <button
                  type="button"
                  onClick={() => onSelectTransaction(txn)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
                >
                  <span className="min-w-0 truncate">
                    <span className="text-muted-foreground text-xs">
                      {formatDate(txn.txn_date)}
                    </span>{" "}
                    <span className="font-medium">{txn.counterparty || txn.raw_description}</span>
                  </span>
                  <span className="shrink-0 font-mono tabular-nums text-rose-600 dark:text-rose-400">
                    {formatCurrency(txn.amount)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
