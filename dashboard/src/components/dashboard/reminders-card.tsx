"use client"

import * as React from "react"
import Link from "next/link"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { getCurrentCalendarMonthYYYYMM } from "@/components/dashboard/date-range-picker"
import { useReminders, useRemindersStatus } from "@/hooks/use-settings"
import { cn, formatCurrency } from "@/lib/utils"
import type { ReminderMonthStatus } from "@/lib/types"

/**
 * Dashboard reminders — uses server-side match status for the current calendar month.
 * No category-total heuristic: without example transactions we only show a mapping warning.
 */
export function RemindersCard() {
  const month = React.useMemo(() => getCurrentCalendarMonthYYYYMM(), [])
  const { data: reminders, isLoading: rLoad } = useReminders()
  const { data: statusRes, isLoading: sLoad } = useRemindersStatus(month)

  const statusById = React.useMemo(() => {
    const m = new Map<number, ReminderMonthStatus>()
    for (const row of statusRes?.items ?? []) {
      m.set(row.reminder_id, row)
    }
    return m
  }, [statusRes])

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Reminders</CardTitle>
        <p className="text-xs text-muted-foreground font-normal">
          Obligations from Settings. Status uses example transactions you picked — calendar month{" "}
          <span className="font-medium text-foreground">{month}</span>.
        </p>
      </CardHeader>
      <CardContent>
        {(rLoad || sLoad) && (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
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
          <ul className="space-y-2">
            {reminders
              .filter((x) => x.is_active)
              .map((r) => {
                const st = statusById.get(r.id)
                return (
                  <li
                    key={r.id}
                    className="flex flex-col gap-1 rounded-lg border border-border px-3 py-1.5 text-sm"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{r.name}</span>
                      <span className="text-xs text-muted-foreground shrink-0">
                        Due day {r.due_day_of_month}
                      </span>
                    </div>
                    {r.amount != null && (
                      <span className="text-xs text-muted-foreground">
                        Expected ~{formatCurrency(r.amount)}
                      </span>
                    )}

                    {!st && <Skeleton className="h-8 w-full" />}

                    {st && !st.has_mapping && (
                      <p
                        className={cn(
                          "rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs",
                          "text-amber-900 dark:text-amber-200",
                        )}
                      >
                        No example transactions mapped — add past payments in{" "}
                        <Link href="/settings" className="underline underline-offset-2">
                          Settings
                        </Link>{" "}
                        so we can match real transfers instead of guessing.
                      </p>
                    )}

                    {st && st.has_mapping && st.examples_stale && (
                      <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1 text-xs text-amber-800 dark:text-amber-200">
                        Some example transactions were deleted — edit this reminder in Settings
                        and re-pick examples.
                      </p>
                    )}

                    {st && st.has_mapping && st.matched_this_month && (
                      <p
                        className="text-xs text-emerald-700 dark:text-emerald-400 truncate"
                        title={st.matched_transactions
                          .map(
                            (t) =>
                              `${t.txn_date} ${t.counterparty ?? ""} ${formatCurrency(t.amount)}`,
                          )
                          .join(" · ")}
                      >
                        <span className="font-medium">Matched this month</span>
                        {st.matched_transactions.map((t, i) => (
                          <span key={t.id}>
                            {i === 0 ? " · " : "; "}
                            {t.txn_date} · {formatCurrency(t.amount)}
                          </span>
                        ))}
                      </p>
                    )}

                    {st &&
                      st.has_mapping &&
                      !st.matched_this_month &&
                      st.unmapped_reason === "no_match_yet" && (
                        <p className="text-xs text-muted-foreground">
                          {r.description_match_anchors.length > 0 ? (
                            <>No matching payment found in {month} yet.</>
                          ) : (
                            <>
                              No matching payment found in {month} yet (same counterparty and
                              similar amount as your examples).
                            </>
                          )}
                        </p>
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
