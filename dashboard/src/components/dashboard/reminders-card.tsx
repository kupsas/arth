"use client"

import * as React from "react"
import Link from "next/link"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCategoryBreakdown } from "@/hooks/use-metrics"
import { useReminders } from "@/hooks/use-settings"
import { formatCurrency } from "@/lib/utils"
import { getPresetRange } from "@/components/dashboard/date-range-picker"

/**
 * Manual reminders (rent, CC due). Optional heuristic: if counterparty_category is set,
 * compare this month's spend in that category to reminder.amount.
 */
export function RemindersCard() {
  const { data: reminders, isLoading: rLoad } = useReminders()
  const thisMonth = React.useMemo(() => getPresetRange("this-month"), [])
  const { data: byCat, isLoading: cLoad } = useCategoryBreakdown(thisMonth, "OUTFLOW")

  function spentInCategory(cat: string | null): number | null {
    if (!cat || !byCat) return null
    const row = byCat.find((r) => r.category === cat)
    return row?.amount ?? 0
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Reminders</CardTitle>
        <p className="text-xs text-muted-foreground font-normal">
          Obligations you configured in Settings. Category spend is this month only.
        </p>
      </CardHeader>
      <CardContent>
        {(rLoad || cLoad) && (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        )}
        {!rLoad && (!reminders || reminders.filter((x) => x.is_active).length === 0) && (
          <p className="text-sm text-muted-foreground">
            No active reminders. Add rent / CC due dates in{" "}
            <Link href="/settings" className="underline underline-offset-2">
              Settings
            </Link>
            .
          </p>
        )}
        {reminders && reminders.filter((x) => x.is_active).length > 0 && (
          <ul className="space-y-3">
            {reminders
              .filter((x) => x.is_active)
              .map((r) => {
                const spent = spentInCategory(r.counterparty_category)
                const paidGuess =
                  r.amount != null &&
                  r.counterparty_category &&
                  spent != null &&
                  spent >= r.amount * 0.85

                return (
                  <li
                    key={r.id}
                    className="flex flex-col gap-0.5 rounded-lg border border-border px-3 py-2 text-sm"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{r.name}</span>
                      <span className="text-xs text-muted-foreground">
                        Due day {r.due_day_of_month}
                      </span>
                    </div>
                    {r.amount != null && (
                      <span className="text-xs text-muted-foreground">
                        Expected ~{formatCurrency(r.amount)}
                        {r.counterparty_category && ` · ${r.counterparty_category}`}
                      </span>
                    )}
                    {r.counterparty_category && spent != null && (
                      <span className="text-xs">
                        Spent this month: {formatCurrency(spent)}
                        {paidGuess && (
                          <span className="ml-2 text-emerald-600 dark:text-emerald-400">
                            (likely covered)
                          </span>
                        )}
                      </span>
                    )}
                  </li>
                )
              })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
