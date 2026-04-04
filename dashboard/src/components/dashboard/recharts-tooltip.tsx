"use client"

/**
 * Recharts default tooltip uses inline styles that ignore Tailwind dark mode.
 * Wrappers here use the same shell as portfolio area / value-trend charts:
 * rounded card, border, theme bg-card, title + muted body lines.
 */

import { formatCurrency, formatPercent } from "@/lib/utils"

/**
 * Shared class for chart hover cards — matches `portfolio-value-trend` and
 * `net-worth-chart` tooltips.
 */
export const RECHARTS_TOOLTIP_CARD_CLASS =
  "rounded-lg border border-border bg-card px-3 py-2 text-xs text-card-foreground shadow-md"

/**
 * Recharts types Payload<ValueType, NameType> where NameType can be string | number.
 * We keep this loose so spreading Tooltip props type-checks.
 */
export interface RechartsTooltipPayload {
  name?: string | number
  value?: unknown
  color?: string
}

/** Coerce Recharts payload values (number | string | other) into a finite number for sums. */
function numericPayloadValue(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (typeof value === "string") {
    const n = Number(value)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}

/**
 * Donut / pie slice hover: series title on top, then INR amount and optional
 * share % from `payload.pct` (computed in chart data).
 *
 * Use `valueMode="percent"` when the slice value is already a 0–100 weight
 * (e.g. holdings allocation %) — then only one amount line is shown.
 */
export function RechartsPieSliceTooltip({
  active,
  payload,
  valueMode = "currency",
  percentDecimals = 1,
  /** E.g. turn `MUTUAL_FUND` into "Mutual fund" for the title line. */
  formatTitle,
}: {
  active?: boolean
  payload?: readonly {
    name?: string | number
    value?: unknown
    payload?: { pct?: number }
  }[]
  valueMode?: "currency" | "percent"
  /** Decimals for `formatPercent` on the third line (currency mode). */
  percentDecimals?: number
  formatTitle?: (rawName: string) => string
}) {
  if (!active || !payload?.[0]) return null
  const item = payload[0]
  const rawName = item.name != null ? String(item.name) : "—"
  const title = formatTitle ? formatTitle(rawName) : rawName
  const raw = item.value
  const num = typeof raw === "number" ? raw : Number(raw ?? 0)
  const row = item.payload as { pct?: number } | undefined

  return (
    <div className={RECHARTS_TOOLTIP_CARD_CLASS}>
      <p className="font-medium leading-tight">{title}</p>
      {valueMode === "currency" ? (
        <>
          <p className="mt-1.5 text-muted-foreground leading-tight">
            {formatCurrency(num)}
          </p>
          {row?.pct != null && (
            <p className="mt-1 text-muted-foreground leading-tight">
              {formatPercent(row.pct, percentDecimals)}
            </p>
          )}
        </>
      ) : (
        <p className="mt-1.5 text-muted-foreground leading-tight">
          {formatPercent(num, percentDecimals)}
        </p>
      )}
    </div>
  )
}

export function RechartsTooltipCard({
  active,
  payload,
  label,
  labelPrefix = "",
  formatValue,
  showTotal = false,
  totalLabel = "Total",
}: {
  active?: boolean
  /** Recharts v3 types `payload` as readonly; keep compatible with spread from <Tooltip content={...} />. */
  payload?: readonly RechartsTooltipPayload[]
  label?: string | number
  labelPrefix?: string
  /** If omitted, values render as plain strings. */
  formatValue?: (value: unknown, name?: string | number) => string
  /**
   * When true, append a row with the sum of all numeric payload values.
   * Useful for stacked bars (e.g. needs + wants = month total in INR or ~100% in share mode).
   */
  showTotal?: boolean
  /** Label for the sum row (default "Total"). */
  totalLabel?: string
}) {
  if (!active || !payload?.length) return null

  const totalSum = showTotal
    ? payload.reduce((acc, entry) => acc + numericPayloadValue(entry.value), 0)
    : null

  return (
    <div className={RECHARTS_TOOLTIP_CARD_CLASS}>
      <p className="font-medium leading-tight">
        {labelPrefix}
        {label != null ? String(label) : ""}
      </p>
      <ul className="mt-1.5 space-y-1">
        {payload.map((entry, i) => (
          <li
            key={i}
            className="flex items-center gap-2 tabular-nums text-muted-foreground leading-tight"
          >
            {entry.color != null && (
              <span
                className="size-2 shrink-0 rounded-sm"
                style={{ backgroundColor: entry.color as string }}
                aria-hidden
              />
            )}
            <span className="min-w-0 flex-1">
              {entry.name != null ? String(entry.name) : "—"}
            </span>
            <span className="shrink-0">
              {formatValue
                ? formatValue(entry.value, entry.name)
                : String(entry.value ?? "")}
            </span>
          </li>
        ))}
        {showTotal && totalSum != null && (
          <li className="flex items-center gap-2 border-t border-border pt-1.5 tabular-nums text-muted-foreground leading-tight">
            <span className="size-2 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">{totalLabel}</span>
            <span className="shrink-0">
              {formatValue
                ? formatValue(totalSum, totalLabel)
                : String(totalSum)}
            </span>
          </li>
        )}
      </ul>
    </div>
  )
}
