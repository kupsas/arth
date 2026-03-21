"use client"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type TrendMonths = 3 | 6 | 12

interface TimeRangeToggleProps {
  value: TrendMonths
  onChange: (m: TrendMonths) => void
  className?: string
}

/** 3M / 6M / 12M switch for the Trends section (defaults to 6 in parent). */
export function TimeRangeToggle({ value, onChange, className }: TimeRangeToggleProps) {
  const opts: { id: TrendMonths; label: string }[] = [
    { id: 3, label: "3M" },
    { id: 6, label: "6M" },
    { id: 12, label: "12M" },
  ]
  return (
    <div className={cn("inline-flex rounded-md border border-border p-0.5 bg-muted/30", className)}>
      {opts.map(({ id, label }) => (
        <Button
          key={id}
          type="button"
          variant={value === id ? "default" : "ghost"}
          size="sm"
          className="h-7 px-3 text-xs"
          onClick={() => onChange(id)}
        >
          {label}
        </Button>
      ))}
    </div>
  )
}
