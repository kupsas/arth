/**
 * Dashboard chart_key values — keep in sync with api/services/chart_metrics.py
 */
export const CHART_KEY_EXPENSE_NEED_WANT_STACK = "expense_need_want_stack" as const
export const CHART_KEY_INVESTMENT_NET = "investment_net" as const

export const CATEGORY_CHART_PREFIX = "category:" as const

export function categoryChartKey(series: string): string {
  return `${CATEGORY_CHART_PREFIX}${series}`
}
