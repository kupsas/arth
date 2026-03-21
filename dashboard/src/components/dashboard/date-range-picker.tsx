/**
 * DateRangePicker — preset pills + custom calendar range.
 *
 * Custom range UX:
 *   - Popover stays open while you pick start and end (no auto-close on click).
 *   - First day click sets "from", second sets "to" (react-day-picker range mode).
 *   - Press **Apply** to commit and close; **Cancel** closes without changing.
 */

"use client"

import * as React from "react"
import { CalendarIcon } from "lucide-react"
import type { DateRange as DayPickerRange } from "react-day-picker"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import type { DateRange } from "@/lib/types"

export type Preset = "all" | "this-month" | "last-month" | "last-3m" | "last-6m" | "custom"

export const PRESETS: { id: Preset; label: string }[] = [
  { id: "this-month",  label: "This Month"    },
  { id: "last-month",  label: "Last Month"    },
  { id: "last-3m",     label: "Last 3 Months" },
  { id: "last-6m",     label: "Last 6 Months" },
]

/**
 * Calendar "YYYY-MM-DD" for the date the user sees — **local** timezone, not UTC.
 *
 * Why not `d.toISOString().split("T")[0]`?
 * `toISOString()` is always UTC. Local midnight on 10 Feb (e.g. India) is still
 * 9 Feb evening in UTC, so you'd get `2025-02-09` and filters would be off by one.
 */
function dateToLocalYYYYMMDD(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

export function getPresetRange(preset: Preset): DateRange {
  const now = new Date()
  switch (preset) {
    case "all":
      return {}
    case "this-month":
      return {
        date_from: dateToLocalYYYYMMDD(new Date(now.getFullYear(), now.getMonth(), 1)),
        date_to:   dateToLocalYYYYMMDD(now),
      }
    case "last-month": {
      const start = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      const end   = new Date(now.getFullYear(), now.getMonth(), 0)
      return { date_from: dateToLocalYYYYMMDD(start), date_to: dateToLocalYYYYMMDD(end) }
    }
    case "last-3m": {
      const start = new Date(now.getFullYear(), now.getMonth() - 3, 1)
      return { date_from: dateToLocalYYYYMMDD(start), date_to: dateToLocalYYYYMMDD(now) }
    }
    case "last-6m": {
      const start = new Date(now.getFullYear(), now.getMonth() - 6, 1)
      return { date_from: dateToLocalYYYYMMDD(start), date_to: dateToLocalYYYYMMDD(now) }
    }
    default:
      return {}
  }
}

export function getPreviousRange(preset: Preset): DateRange {
  if (preset === "all") return {}
  const now = new Date()
  switch (preset) {
    case "this-month": {
      const start = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      const end   = new Date(now.getFullYear(), now.getMonth(), 0)
      return { date_from: dateToLocalYYYYMMDD(start), date_to: dateToLocalYYYYMMDD(end) }
    }
    case "last-month": {
      const start = new Date(now.getFullYear(), now.getMonth() - 2, 1)
      const end   = new Date(now.getFullYear(), now.getMonth() - 1, 0)
      return { date_from: dateToLocalYYYYMMDD(start), date_to: dateToLocalYYYYMMDD(end) }
    }
    case "last-3m": {
      const start = new Date(now.getFullYear(), now.getMonth() - 6, 1)
      const end   = new Date(now.getFullYear(), now.getMonth() - 3, 0)
      return { date_from: dateToLocalYYYYMMDD(start), date_to: dateToLocalYYYYMMDD(end) }
    }
    case "last-6m": {
      const start = new Date(now.getFullYear(), now.getMonth() - 12, 1)
      const end   = new Date(now.getFullYear(), now.getMonth() - 6, 0)
      return { date_from: dateToLocalYYYYMMDD(start), date_to: dateToLocalYYYYMMDD(end) }
    }
    default:
      return {}
  }
}

function formatRange(range: DateRange): string {
  if (!range.date_from || !range.date_to) return "Custom"
  const fmt = (iso: string) =>
    new Date(iso + "T00:00:00").toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
    })
  return `${fmt(range.date_from)} – ${fmt(range.date_to)}`
}

interface DateRangePickerProps {
  preset: Preset
  customRange: DateRange
  onPresetChange: (preset: Preset, range: DateRange) => void
  onCustomChange: (range: DateRange) => void
  clearable?: boolean
  className?: string
}

export function DateRangePicker({
  preset,
  customRange,
  onPresetChange,
  onCustomChange,
  clearable = false,
  className,
}: DateRangePickerProps) {
  const [open, setOpen] = React.useState(false)
  const [calRange, setCalRange] = React.useState<DayPickerRange | undefined>(undefined)

  // When the popover opens, copy the last applied custom range into the calendar (or start empty).
  React.useEffect(() => {
    if (!open) return
    if (customRange.date_from && customRange.date_to) {
      setCalRange({
        from: new Date(customRange.date_from + "T00:00:00"),
        to:   new Date(customRange.date_to + "T00:00:00"),
      })
    } else {
      setCalRange(undefined)
    }
  }, [open])

  function handleCalendarSelect(range: DayPickerRange | undefined) {
    setCalRange(range)
  }

  function handleApply() {
    if (calRange?.from && calRange?.to) {
      onCustomChange({
        date_from: dateToLocalYYYYMMDD(calRange.from),
        date_to:   dateToLocalYYYYMMDD(calRange.to),
      })
      setOpen(false)
    }
  }

  const canApply = Boolean(calRange?.from && calRange?.to)

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {PRESETS.map((p) => (
        <Button
          key={p.id}
          variant={preset === p.id ? "default" : "outline"}
          size="sm"
          onClick={() => {
            if (clearable && preset === p.id) {
              onPresetChange("all", {})
            } else {
              onPresetChange(p.id, getPresetRange(p.id))
            }
          }}
          className="h-8 text-xs"
        >
          {p.label}
        </Button>
      ))}

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <Button
              variant={preset === "custom" ? "default" : "outline"}
              size="sm"
              className="h-8 gap-1.5 text-xs"
            />
          }
        >
          <CalendarIcon className="size-3.5" />
          {preset === "custom" ? formatRange(customRange) : "Custom"}
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0 flex flex-col" align="end">
          <Calendar
            mode="range"
            selected={calRange}
            onSelect={handleCalendarSelect}
            numberOfMonths={2}
            disabled={{ after: new Date() }}
          />
          <div className="flex items-center justify-between gap-2 border-t border-border p-2">
            <p className="text-[11px] text-muted-foreground px-1">
              {calRange?.from && !calRange?.to
                ? "Pick end date"
                : !calRange?.from
                  ? "Pick start date"
                  : "Adjust or Apply"}
            </p>
            <div className="flex gap-1.5">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 text-xs"
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                size="sm"
                className="h-8 text-xs"
                disabled={!canApply}
                onClick={handleApply}
              >
                Apply
              </Button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}
