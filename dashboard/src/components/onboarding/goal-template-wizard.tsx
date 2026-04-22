"use client"

/**
 * Template-based **goal** wizard (Track 2 Phase 4c).
 *
 * - Picks a template (house, car, retirement, … or custom).
 * - Lets the user set target amount, horizon in years, and a concrete target date.
 * - Fetches `GET /api/onboarding/goal-templates` for inflation defaults + a live
 *   future-value **preview** (target in today's rupees → inflation-adjusted stub).
 * - Submits with `useCreateGoal` → `POST /api/goals` using the same schema as
 *   the main Goals table.
 *
 * The preview math is intentionally simple display logic; the full simulation
 * engine still runs elsewhere when you use surplus / what-if features.
 */

import * as React from "react"
import { Loader2, Sparkles } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCreateGoal } from "@/hooks/use-goals"
import { useOnboardingGoalTemplates } from "@/hooks/use-onboarding-goal-templates"
import { cn } from "@/lib/utils"
import type { OnboardingGoalTemplate, GoalCreate } from "@/lib/types"

function addYears(d: Date, y: number): string {
  const t = new Date(d)
  t.setFullYear(t.getFullYear() + Math.max(0, y))
  return t.toISOString().slice(0, 10)
}

function buildCreatePayload(
  tpl: OnboardingGoalTemplate,
  targetAmount: number,
  _years: number,
  targetDateIso: string,
): GoalCreate {
  // — Yearly travel budget: recurring inflow/accumulation, not a single end lump-sum.
  if (tpl.id === "travel" || tpl.goal_class === "RECURRING_CASH_FLOW") {
    return {
      name: tpl.name,
      goal_type: "SAVINGS",
      target_amount: targetAmount,
      target_date: targetDateIso,
      priority: tpl.suggested_priority,
      time_horizon: "ANNUAL",
      funding_mode: "EVENT",
      goal_class: "RECURRING_CASH_FLOW",
      goal_subtype: "TRAVEL",
      recurrence_amount: targetAmount,
      recurrence_frequency: "ANNUAL",
      recurrence_start: new Date().toISOString().slice(0, 10),
      expected_return_rate: tpl.default_expected_return_rate,
      goal_specific_inflation_rate: tpl.inflation_annual_percent,
    }
  }

  return {
    name: tpl.name,
    goal_type: tpl.goal_type as GoalCreate["goal_type"],
    target_amount: targetAmount,
    target_date: targetDateIso,
    priority: tpl.suggested_priority,
    time_horizon: (tpl.time_horizon ?? "MULTI_YEAR") as GoalCreate["time_horizon"],
    funding_mode: (tpl.funding_mode ?? "ACCUMULATION") as GoalCreate["funding_mode"],
    goal_class: (tpl.goal_class ?? "POINT_IN_TIME") as GoalCreate["goal_class"],
    goal_subtype: (tpl.goal_subtype ?? "CUSTOM") as GoalCreate["goal_subtype"],
    expected_return_rate: tpl.default_expected_return_rate,
    // Seed for display — the goals API can still re-resolve from subtype.
    goal_specific_inflation_rate: tpl.inflation_annual_percent,
  }
}

export function GoalTemplateWizard() {
  const { mutate: create, isPending, isError, error } = useCreateGoal()
  const [selected, setSelected] = React.useState<string | null>(null)
  const [amount, setAmount] = React.useState(50_00_000)
  const [years, setYears] = React.useState(5)
  const [done, setDone] = React.useState(false)

  const { data, isLoading } = useOnboardingGoalTemplates(
    { target_amount: amount, years, template_id: selected ?? undefined },
    { enabled: true },
  )

  const extraHeadline = data?.headline_preview
  const templates = data?.templates ?? []
  const active: OnboardingGoalTemplate | undefined = templates.find(
    (t) => t.id === selected,
  )
  const preview = active?.preview ?? (selected == null ? extraHeadline : undefined)
  const targetDate = addYears(new Date(), years)

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <Sparkles className="size-6" />
          Goals
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          Pick a template, tune the target (amount + timeline), and save.  Amounts
          are in <strong>today’s rupees</strong>; the preview on the right shows
          a rough <strong>inflation-adjusted</strong> end value so expectations line
          up with long-range planning.
        </p>
      </div>

      {isLoading && !data && (
        <p className="text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 className="size-4 animate-spin" />
          Loading templates and CPI hints…
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {templates
          .filter((t) => t.id !== "custom")
          .map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setSelected(t.id)
                // Snap defaults to template band mid-point so the first preview feels sane.
                const mid =
                  (t.default_target_amount_min + t.default_target_amount_max) / 2
                setAmount(Math.round(mid))
                setYears(
                  Math.round(
                    (t.default_timeframe_years_min + t.default_timeframe_years_max) / 2,
                  ),
                )
              }}
              className={cn(
                "text-left rounded-xl border p-4 transition-colors hover:bg-muted/50",
                selected === t.id && "ring-2 ring-primary",
              )}
            >
              <div className="text-2xl mb-1" aria-hidden>
                {t.icon}
              </div>
              <div className="font-medium">{t.name}</div>
              <p className="text-xs text-muted-foreground mt-1">
                ~₹{t.default_target_amount_min / 1e5}L–{t.default_target_amount_max / 1e5}L ·
                {t.inflation_annual_percent}%/yr {t.inflation_rate_label}
              </p>
            </button>
          ))}

        <button
          type="button"
          onClick={() => {
            setSelected("custom")
            setAmount(5_00_000)
            setYears(3)
          }}
          className={cn(
            "text-left rounded-xl border p-4 border-dashed transition-colors hover:bg-muted/50",
            selected === "custom" && "ring-2 ring-primary",
          )}
        >
          <div className="text-2xl mb-1" aria-hidden>
            ✨
          </div>
          <div className="font-medium">Custom</div>
          <p className="text-xs text-muted-foreground mt-1">Define your own</p>
        </button>
      </div>

      {active && (
        <Card>
          <CardHeader>
            <CardTitle>
              {active.icon} {active.name}
            </CardTitle>
            <CardDescription>
              Defaults use category-specific inflation keys from the server (same
              data as the main inflation screen).
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 sm:max-w-md">
            <div className="space-y-2">
              <Label htmlFor="amt">Target amount (today’s ₹)</Label>
              <Input
                id="amt"
                type="number"
                min={1}
                step={1000}
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value) || 0)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="yrs">Horizon (years)</Label>
              <Input
                id="yrs"
                type="number"
                min={0.1}
                step={0.5}
                value={years}
                onChange={(e) => setYears(Number(e.target.value) || 0)}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Suggested target date (auto, edit later in Goals if you like):{" "}
              <span className="font-mono">{targetDate}</span>
            </p>
          </CardContent>
          {preview && (
            <div className="px-6 pb-2 text-sm text-muted-foreground border-t border-border/50 pt-4">
              {preview.copy}
            </div>
          )}
          <CardFooter>
            <Button
              type="button"
              disabled={isPending || !active}
              onClick={() => {
                if (!active) return
                const body = buildCreatePayload(active, amount, years, targetDate)
                create(body, { onSuccess: () => setDone(true) })
              }}
            >
              {isPending ? "Saving…" : "Create goal"}
            </Button>
            {done && (
              <p className="ml-3 text-sm text-emerald-600" role="status">
                Added — you can continue in Goals for fine-tuning.
              </p>
            )}
            {isError && (
              <p className="ml-3 text-sm text-destructive" role="alert">
                {String(error ?? "Create failed")}
              </p>
            )}
          </CardFooter>
        </Card>
      )}

      {selected == null && extraHeadline && (
        <p className="text-xs text-muted-foreground border-t pt-3">{extraHeadline.copy}</p>
      )}
    </div>
  )
}
