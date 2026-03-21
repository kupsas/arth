"use client"

import { Check, X } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { useGoalProgress } from "@/hooks/use-metrics"
import { formatCurrency } from "@/lib/utils"
import Link from "next/link"

interface SingleGoalProps {
  goalId: number
  title: string
}

function SingleGoalBar({ goalId, title }: SingleGoalProps) {
  const { data, isLoading, isError } = useGoalProgress(goalId)

  if (isLoading) {
    return (
      <div className="space-y-2 py-2">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-2 w-full" />
        <Skeleton className="h-6 w-full" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <p className="text-sm text-muted-foreground py-2">
        Could not load progress for this goal.
      </p>
    )
  }

  const target = data.target_amount
  const pct =
    target && target > 0
      ? Math.min(100, Math.round((data.current_value / target) * 100))
      : 0

  return (
    <div className="space-y-3 border-b border-border pb-4 last:border-0 last:pb-0">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            {formatCurrency(data.current_value)}
            {target != null && target > 0 ? (
              <> of {formatCurrency(target)} this month</>
            ) : (
              <> this month (set a target on the Goals page)</>
            )}
          </p>
          {data.goal_type === "INVESTMENT" &&
            data.purchases != null &&
            data.sales != null && (
              <p className="text-[11px] text-muted-foreground mt-1">
                Purchases {formatCurrency(data.purchases)} · Sales{" "}
                {formatCurrency(data.sales)} · Net {formatCurrency(data.net_investment ?? 0)}
              </p>
            )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {data.adherence.map((m) => (
            <span
              key={m.month}
              title={`${m.month}: ${m.hit == null ? "n/a" : m.hit ? "met" : "missed"}`}
              className="flex size-7 items-center justify-center rounded border border-border bg-muted/40"
            >
              {m.hit == null ? (
                <span className="text-[10px] text-muted-foreground">—</span>
              ) : m.hit ? (
                <Check className="size-3.5 text-emerald-600" />
              ) : (
                <X className="size-3.5 text-rose-600" />
              )}
            </span>
          ))}
        </div>
      </div>
      {target != null && target > 0 && (
        <Progress value={Math.min(100, pct)} className="h-2" />
      )}
    </div>
  )
}

/**
 * Shows the two headline monthly goals: Investment + total expense cap.
 * Picks the first matching goal from the list (configure order on Goals page via priority later).
 */
export function GoalProgressSection(props: {
  investmentGoalId: number | null
  expenseGoalId: number | null
}) {
  const { investmentGoalId, expenseGoalId } = props

  if (!investmentGoalId && !expenseGoalId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">This month so far</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No <strong>INVESTMENT</strong> or <strong>EXPENSE_LIMIT</strong> (total) goals yet.{" "}
            <Link href="/goals" className="underline underline-offset-2 text-foreground">
              Create them on the Goals page
            </Link>
            .
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">This month so far</CardTitle>
        <p className="text-xs text-muted-foreground font-normal">
          Goal progress and last 4 months adherence (✓ / ✗).
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        {investmentGoalId != null && (
          <SingleGoalBar goalId={investmentGoalId} title="Investment" />
        )}
        {expenseGoalId != null && (
          <SingleGoalBar goalId={expenseGoalId} title="Total expenses" />
        )}
      </CardContent>
    </Card>
  )
}
