"use client"

/**
 * Recharts default tooltip uses inline styles that ignore Tailwind dark mode.
 * This wrapper uses popover tokens so text/background match the theme.
 */

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
    <div className="rounded-md border border-border bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-md">
      <p className="mb-1 font-medium text-foreground">
        {labelPrefix}
        {label != null ? String(label) : ""}
      </p>
      <ul className="space-y-0.5">
        {payload.map((entry, i) => (
          <li key={i} className="flex items-center gap-2 tabular-nums">
            {entry.color != null && (
              <span
                className="size-2 shrink-0 rounded-sm"
                style={{ backgroundColor: entry.color as string }}
                aria-hidden
              />
            )}
            <span className="text-muted-foreground">
              {entry.name != null ? String(entry.name) : "—"}:
            </span>
            <span className="font-medium text-foreground">
              {formatValue
                ? formatValue(entry.value, entry.name)
                : String(entry.value ?? "")}
            </span>
          </li>
        ))}
        {showTotal && totalSum != null && (
          <li className="mt-1 flex items-center gap-2 border-t border-border pt-1 tabular-nums">
            {/* Spacer aligns label with series names (same width as color swatch column). */}
            <span className="size-2 shrink-0" aria-hidden />
            <span className="text-muted-foreground">{totalLabel}:</span>
            <span className="font-medium text-foreground">
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
