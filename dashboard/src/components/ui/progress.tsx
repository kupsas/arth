"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Value from 0 to 100. Pass undefined for an indeterminate / animated state. */
  value?: number
}

/**
 * Simple progress bar component.
 * When `value` is undefined the bar plays a pulse animation (indeterminate state).
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
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-full bg-secondary",
        className,
      )}
      {...props}
    >
      <div
        className={cn(
          "h-full rounded-full bg-primary transition-all",
          isIndeterminate && "animate-pulse w-full opacity-60",
        )}
        style={isIndeterminate ? {} : { width: `${Math.min(Math.max(value ?? 0, 0), 100)}%` }}
      />
    </div>
  )
}

export { Progress }
