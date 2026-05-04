/**
 * UI "goal kind" — primary selector on the Goals sheet.
 *
 * The API still stores `goal_type` (product category) and optional `goal_class`
 * (simulation shape). We derive `goal_type` from the user's kind choice so the
 * server contract stays unchanged.
 *
 * - POINT_IN_TIME / RECURRING_CASH_FLOW → map to goal_class + goal_type
 * - EXPENSE_LIMIT → only goal_type (chart-linked spend caps); goal_class stays unset
 */

import type { Goal } from "@/lib/types"
import type { GoalCreate, GoalType } from "@/lib/types"

import {
  CHART_KEY_EXPENSE_NEED_WANT_STACK,
  CHART_KEY_INVESTMENT_NET,
} from "@/lib/chart-keys"

/** Three simulation shapes in the dashboard + spending cap (legacy EXPENSE_LIMIT). */
export type GoalUiKind =
  | "POINT_IN_TIME"
  | "RECURRING_CASH_FLOW"
  | "EXPENSE_LIMIT"

export const GOAL_UI_KIND_LABELS: Record<GoalUiKind, string> = {
  POINT_IN_TIME: "One-time or investment target (lump sum)",
  RECURRING_CASH_FLOW: "Recurring cash flow (EMI, rent…)",
  EXPENSE_LIMIT: "Spending cap (budget)",
}

/** Form state for Add Goal — keeps UI fields separate from API until submit. */
export interface AddGoalFormState {
  uiKind: GoalUiKind
  name: string
  notes: string
  priority: number
  linked_layer: number
  /** POINT_IN_TIME, EXPENSE_LIMIT */
  target_amount?: number
  target_date?: string
  /** POINT_IN_TIME */
  starting_balance?: number
  goal_specific_inflation_rate?: number
  /** POINT_IN_TIME (optional; defaults on server if unset) */
  expected_return_rate?: number
  /** RECURRING_CASH_FLOW */
  recurrence_amount?: number
  recurrence_frequency: "MONTHLY" | "QUARTERLY" | "ANNUAL"
  recurrence_start?: string
  recurrence_end?: string
  goal_subtype?: string
  /** EXPENSE_LIMIT */
  progress_cadence: "MONTHLY" | "ANNUAL"
  /**
   * When opening from a chart deep-link (e.g. category:food_and_dining), bind the cap
   * to that chart; otherwise defaults to the need/want stack.
   */
  chart_key?: string | null
}

export function defaultAddGoalForm(): AddGoalFormState {
  return {
    uiKind: "POINT_IN_TIME",
    name: "",
    notes: "",
    priority: 3,
    linked_layer: 3,
    recurrence_frequency: "MONTHLY",
    progress_cadence: "MONTHLY",
    /** Drives default inflation bucket (REAL_ESTATE, TRAVEL_DOMESTIC, …) — set at create only. */
    goal_subtype: "CUSTOM",
  }
}

/**
 * Maps simulation / API goal_class string to storage goal_type (same rules as Save simulation).
 */
export function simulationGoalClassToGoalType(gc: string): GoalType {
  const u = gc.toUpperCase()
  if (u === "RECURRING_CASH_FLOW") return "DEBT_PAYOFF"
  return "SAVINGS"
}

/**
 * Infer UI kind for display / edit from persisted goal (handles legacy rows without goal_class).
 */
export function inferGoalUiKind(goal: Goal): GoalUiKind {
  if (goal.goal_type === "EXPENSE_LIMIT") return "EXPENSE_LIMIT"
  if (goal.goal_class === "RECURRING_CASH_FLOW") return "RECURRING_CASH_FLOW"
  if (goal.goal_class === "POINT_IN_TIME") return "POINT_IN_TIME"
  if (goal.goal_type === "INVESTMENT") return "POINT_IN_TIME"
  if (goal.goal_type === "DEBT_PAYOFF") return "RECURRING_CASH_FLOW"
  return "POINT_IN_TIME"
}

export function labelGoalUiKind(kind: GoalUiKind): string {
  return GOAL_UI_KIND_LABELS[kind]
}

/**
 * Build POST /api/goals body from the add form. Applies chart_key defaults for first
 * investment / expense stack links (server may clear duplicate investment_net).
 */
export function addGoalFormToCreatePayload(form: AddGoalFormState): GoalCreate {
  const name = form.name.trim()
  const notes = form.notes.trim() ? form.notes.trim() : undefined
  const base: GoalCreate = {
    name,
    goal_type: "SAVINGS",
    priority: form.priority,
    linked_layer: form.linked_layer,
    notes,
  }

  switch (form.uiKind) {
    case "EXPENSE_LIMIT":
      return {
        ...base,
        goal_type: "EXPENSE_LIMIT",
        target_amount: form.target_amount,
        target_date: form.target_date || undefined,
        progress_cadence: form.progress_cadence,
        chart_key: form.chart_key ?? CHART_KEY_EXPENSE_NEED_WANT_STACK,
      }
    case "POINT_IN_TIME": {
      const fromInvestmentChart = form.chart_key === CHART_KEY_INVESTMENT_NET
      return {
        ...base,
        goal_type: fromInvestmentChart ? "INVESTMENT" : "SAVINGS",
        goal_class: "POINT_IN_TIME",
        target_amount: form.target_amount,
        target_date: form.target_date || undefined,
        starting_balance: form.starting_balance,
        goal_specific_inflation_rate: form.goal_specific_inflation_rate,
        expected_return_rate: form.expected_return_rate,
        current_value: form.starting_balance,
        goal_subtype: form.goal_subtype || "CUSTOM",
        chart_key: fromInvestmentChart ? CHART_KEY_INVESTMENT_NET : undefined,
      }
    }
    case "RECURRING_CASH_FLOW":
      return {
        ...base,
        goal_type: "DEBT_PAYOFF",
        goal_class: "RECURRING_CASH_FLOW",
        recurrence_amount: form.recurrence_amount,
        recurrence_frequency: form.recurrence_frequency,
        recurrence_start: form.recurrence_start || undefined,
        recurrence_end: form.recurrence_end || undefined,
        goal_subtype: form.goal_subtype || undefined,
      }
  }
}

/** Populate add form when opening from a dashboard chart deep-link. */
export function prefillAddFormForChartKey(
  chartKey: string | null | undefined,
): Partial<AddGoalFormState> {
  if (chartKey === CHART_KEY_INVESTMENT_NET) {
    return { uiKind: "POINT_IN_TIME", chart_key: CHART_KEY_INVESTMENT_NET }
  }
  if (chartKey === CHART_KEY_EXPENSE_NEED_WANT_STACK || chartKey?.startsWith("category:")) {
    return { uiKind: "EXPENSE_LIMIT", chart_key: chartKey ?? undefined }
  }
  return {}
}
