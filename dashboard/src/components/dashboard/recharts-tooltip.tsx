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

export function RechartsTooltipCard({
  active,
  payload,
  label,
  labelPrefix = "",
  formatValue,
}: {
  active?: boolean
  payload?: RechartsTooltipPayload[]
  label?: string | number
  labelPrefix?: string
  /** If omitted, values render as plain strings. */
  formatValue?: (value: unknown, name?: string | number) => string
}) {
  if (!active || !payload?.length) return null

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
      </ul>
    </div>
  )
}
