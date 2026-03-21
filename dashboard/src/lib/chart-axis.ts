/**
 * Y-axis bounds for dashboard bar charts — data-driven like the category mini-charts,
 * instead of Recharts defaults + goal-line domain extension (which pinned axes to e.g. 6L).
 */

/** Upper bound for stacked need+want amounts (amount mode, not %). */
export function expenseStackedYAxisMax(
  rows: ReadonlyArray<{ need: number; want: number }>,
): number {
  if (rows.length === 0) return 1
  const stacks = rows.map(
    (r) => Math.max(0, Number(r.need) || 0) + Math.max(0, Number(r.want) || 0),
  )
  const peak = Math.max(1, ...stacks)
  return peak * 1.12
}

/** Domain for net investment bars (handles negative net). Always includes 0 when possible. */
export function investmentNetYAxisDomain(
  rows: ReadonlyArray<{ net: number }>,
): [number, number] {
  if (rows.length === 0) return [-1, 1]
  const nets = rows.map((r) => Number(r.net) || 0)
  const hiN = Math.max(...nets)
  const loN = Math.min(...nets)
  const span = Math.max(hiN - loN, 1)
  const pad = Math.max(span * 0.08, 1)
  let hi = hiN + pad
  let lo = loN - pad
  lo = Math.min(lo, 0)
  hi = Math.max(hi, 0)
  if (hi <= lo) return [lo - 1, hi + 1]
  return [lo, hi]
}
