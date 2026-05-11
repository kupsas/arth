"use client"

/**
 * Shared copy for goal targets: amounts are in **today's rupees**; optional line for
 * nominal future rupees at the deadline when inflation &gt; 0.
 */

import * as React from "react"

import {
  DEFAULT_HEADLINE_INFLATION_PCT,
  explainGoalTargetMoney,
  type InflationResolutionLike,
} from "@/lib/goal-target-money"
import { simulationInflationResolutionFromGoal } from "@/lib/goal-inflation-preview"
import type { SimulationGoal } from "@/lib/types"
import { formatCurrency, parseInrMoneyInput } from "@/lib/utils"

function parseInflationInput(s: string): number | null | undefined {
  const t = s.trim()
  if (t === "") return undefined
  const n = parseFloat(t)
  return Number.isNaN(n) ? undefined : n
}

function parseTargetInput(s: string): number | null {
  return parseInrMoneyInput(s)
}

type GoalTargetMoneyHintProps = {
  /** Raw input from the target amount field */
  rawTargetInput: string
  targetDate: string
  /** Optional goal-specific inflation field — empty means use headline */
  goalSpecificInflationInput: string
  /** Headline CPI when goal inflation is empty (simulation slider or default 6%). */
  headlineInflationPct?: number
  /** Server resolution (category EMA vs CPI EMA) — from GET /api/goals */
  inflationResolution?: InflationResolutionLike | null
  className?: string
}

/**
 * Use under target + deadline fields for one-time / growth goals.
 */
export function GoalTargetMoneyHint({
  rawTargetInput,
  targetDate,
  goalSpecificInflationInput,
  headlineInflationPct = DEFAULT_HEADLINE_INFLATION_PCT,
  inflationResolution,
  className,
}: GoalTargetMoneyHintProps) {
  const raw = parseTargetInput(rawTargetInput)
  const goalInfl = parseInflationInput(goalSpecificInflationInput)
  const expl =
    raw != null && raw > 0 && targetDate.trim()
      ? explainGoalTargetMoney({
          rawTarget: raw,
          targetDateISO: targetDate,
          goalSpecificInflation: goalInfl === undefined ? null : goalInfl,
          headlineInflationPct,
          inflationResolution: inflationResolution ?? null,
        })
      : null

  if (!expl?.showTodaysMoneyLine) {
    return (
      <p className={className ?? "text-xs text-muted-foreground leading-relaxed"}>
        Enter the target in <span className="font-medium text-foreground">today&apos;s rupees</span>{" "}
        — what that money buys <span className="font-medium text-foreground">today</span>. Plans and
        simulations then grow it toward a nominal future number for contributions.
      </p>
    )
  }

  return (
    <div
      className={className ?? "space-y-2 text-xs text-muted-foreground leading-relaxed"}
    >
      <p>
        This target is in {" "}
        <span className="font-medium text-foreground"> today&apos;s rupees</span>. We grow it by inflation for planning.
      </p>
      {expl.planningNote ? (
        <p className="rounded-md border border-dashed border-border/80 bg-muted/30 px-2.5 py-1.5 text-[11px] sm:text-xs">
          {expl.planningNote}
        </p>
      ) : null}
      {expl.showNominalFutureLine ? (
        <p className="rounded-md border border-border/80 bg-muted/40 px-2.5 py-2 text-[11px] sm:text-xs">
          {expl.emphasizeLongHorizon ? (
            <>
              With <span className="font-medium text-foreground">{expl.effectiveInflationPct}%</span>{" "}
              annual inflation, by{" "}
              <span className="font-medium text-foreground">{expl.formattedDeadline}</span>, you&apos;d
              need about{" "}
              <span className="font-medium text-foreground">
                {formatCurrency(expl.nominalAtDate)}
              </span>{" "}
              to match roughly the same lifestyle in rupee terms.
            </>
          ) : (
            <>
              At{" "}
              <span className="font-medium text-foreground">{expl.formattedDeadline}</span>, nominal
              target ≈{" "}
              <span className="font-medium text-foreground">
                {formatCurrency(expl.nominalAtDate)}
              </span>{" "}
              ({expl.effectiveInflationPct}% inflation / year).
            </>
          )}
        </p>
      ) : null}
    </div>
  )
}

/** Expanded simulation card — uses slider headline when goal inflation is empty. */
export function SimulationGoalTargetMoneyHint({
  goal,
  generalInflationRate,
}: {
  goal: SimulationGoal
  generalInflationRate: number
}) {
  const raw = goal.target_amount ?? 0
  const td = goal.target_date?.trim() ?? ""
  const resolution = simulationInflationResolutionFromGoal(goal, generalInflationRate)
  const expl =
    raw > 0 && td
      ? explainGoalTargetMoney({
          rawTarget: raw,
          targetDateISO: td,
          goalSpecificInflation: null,
          headlineInflationPct: generalInflationRate,
          inflationResolution: resolution,
        })
      : null

  if (!expl?.showTodaysMoneyLine) {
    return (
      <p className="text-[11px] text-muted-foreground leading-relaxed">
        Target uses <span className="font-medium text-foreground">today&apos;s rupees</span>. Leave
        inflation blank to follow your goal type (or the general inflation slider for broad CPI).
      </p>
    )
  }

  return (
    <div className="space-y-2 text-[11px] text-muted-foreground leading-relaxed">
      <p>
        Target is in <span className="font-medium text-foreground">today&apos;s rupees</span> (what
        things roughly cost right now).
      </p>
      {expl.planningNote ? (
        <p className="rounded-md border border-dashed border-border/80 bg-muted/30 px-2 py-1.5">
          {expl.planningNote}
        </p>
      ) : null}
      {expl.showNominalFutureLine ? (
        <p className="rounded-md border border-border/80 bg-muted/40 px-2 py-1.5">
          {expl.emphasizeLongHorizon ? (
            <>
              With <span className="font-medium text-foreground">{expl.effectiveInflationPct}%</span>{" "}
              / year, by {expl.formattedDeadline} you&apos;d need about{" "}
              <span className="font-medium text-foreground">
                {formatCurrency(expl.nominalAtDate)}
              </span>{" "}
              nominal to match that lifestyle in rupee terms.
            </>
          ) : (
            <>
              At {expl.formattedDeadline}, nominal ≈{" "}
              <span className="font-medium text-foreground">
                {formatCurrency(expl.nominalAtDate)}
              </span>{" "}
              ({expl.effectiveInflationPct}% / yr).
            </>
          )}
        </p>
      ) : null}
    </div>
  )
}

/** Compact one-liner for goal cards (list view). */
export function GoalTargetMoneyCardLine({
  rawTarget,
  targetDate,
  goalSpecificInflation,
  headlineInflationPct = DEFAULT_HEADLINE_INFLATION_PCT,
  inflationResolution,
}: {
  rawTarget: number | null
  targetDate: string | null
  goalSpecificInflation: number | null | undefined
  headlineInflationPct?: number
  inflationResolution?: InflationResolutionLike | null
}) {
  if (rawTarget == null || rawTarget <= 0 || !targetDate?.trim()) {
    return null
  }

  const expl = explainGoalTargetMoney({
    rawTarget,
    targetDateISO: targetDate,
    goalSpecificInflation: goalSpecificInflation ?? null,
    headlineInflationPct,
    inflationResolution: inflationResolution ?? null,
  })

  if (!expl) return null

  return (
    <p className="text-[11px] text-muted-foreground leading-snug border-t border-border/60 pt-2 mt-1">
      Target{" "}
      <span className="font-medium text-foreground">{formatCurrency(rawTarget)}</span>
      {" "}
      is in today&apos;s rupees
      {expl.planningNote ? (
        <>
          {" "}
          <span className="italic opacity-90">({expl.planningNote})</span>
        </>
      ) : null}
      {expl.showNominalFutureLine ? (
        <>
          {" "}
          {expl.emphasizeLongHorizon ? (
            <>
              By {expl.formattedDeadline}, plan for about{" "}
              <span className="font-medium text-foreground">
                {formatCurrency(expl.nominalAtDate)}
              </span>{" "}.
            </>
          ) : (
            <>
              At {expl.formattedDeadline}, ≈{" "}
              <span className="font-medium text-foreground">
                {formatCurrency(expl.nominalAtDate)}
              </span>{" "}
              nominal ({expl.effectiveInflationPct}% / yr).
            </>
          )}
        </>
      ) : null}
    </p>
  )
}
