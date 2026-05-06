"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Value from 0 to 100. Pass undefined for an indeterminate / animated state. */
  value?: number
}

/**
 * Simple progress bar component.
 * When `value` is undefined the bar shows a sliding segment (indeterminate state).
 * Indeterminate uses a **partial** width segment — a full-width pulse looked like 100% complete.
 */
function Progress({ value, className, ...props }: ProgressProps) {
  const isIndeterminate = value === undefined

  return (
    <div
      data-slot="progress"
      role="progressbar"
      aria-valuenow={isIndeterminate ? undefined : value}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-busy={isIndeterminate ? true : undefined}
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-full bg-secondary",
        className,
      )}
      {...props}
    >
      <div
        className={cn(
          "h-full rounded-full bg-primary transition-[width] duration-300 ease-out",
          isIndeterminate &&
            "absolute left-0 top-0 w-[38%] motion-safe:animate-[arth-progress-slide_1.35s_ease-in-out_infinite]",
        )}
        style={isIndeterminate ? undefined : { width: `${Math.min(Math.max(value ?? 0, 0), 100)}%` }}
      />
    </div>
  )
}

export { Progress }
