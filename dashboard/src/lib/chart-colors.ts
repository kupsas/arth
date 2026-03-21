/**
 * Chart colours for Recharts — use CSS variables (oklch) directly.
 *
 * IMPORTANT: Do not wrap these in `hsl()` — theme tokens are oklch, not hsl triples.
 * `hsl(var(--chart-1))` is invalid CSS and browsers paint bars black.
 */

import type { DashboardCategorySeries } from "@/lib/types"

/** Investment chart: purchases (green family) vs sales (red family). */
export const CHART_PURCHASE = "var(--chart-purchase)"
export const CHART_SALE = "var(--chart-sale)"

/** Stacked expenses: needs vs wants. */
export const CHART_NEED = "var(--chart-need)"
export const CHART_WANT = "var(--chart-want)"

/** Goal / reference line. */
export const CHART_GOAL_LINE = "var(--chart-goal-line)"

/** Distinct hue per category mini-chart (light + .dark overrides in globals.css). */
export const CATEGORY_SERIES_COLOR: Record<DashboardCategorySeries, string> = {
  swiggy_instamart: "var(--chart-cat-1)",
  swiggy_food: "var(--chart-cat-2)",
  food_and_dining: "var(--chart-cat-3)",
  shopping: "var(--chart-cat-5)",
  transport: "var(--chart-cat-6)",
  travel: "var(--chart-cat-7)",
}
