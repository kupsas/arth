/**
 * grouping-toggle.tsx — pill-style selector reused by equities + mutual funds.
 *
 * We use real <button> elements (via shadcn Button) so keyboard and screen
 * readers get proper focus rings without wiring full WAI-ARIA tabs.
 */

"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface GroupingToggleOption<T extends string> {
  value: T;
  label: string;
}

export interface GroupingToggleProps<T extends string> {
  options: GroupingToggleOption<T>[];
  value: T;
  onChange: (next: T) => void;
  className?: string;
  /** Optional hint shown on smaller screens (e.g. deferred feature note). */
  hint?: string;
}

export function GroupingToggle<T extends string>({
  options,
  value,
  onChange,
  className,
  hint,
}: GroupingToggleProps<T>) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt) => (
          <Button
            key={opt.value}
            type="button"
            size="sm"
            variant={value === opt.value ? "default" : "outline"}
            className="rounded-full"
            onClick={() => onChange(opt.value)}
          >
            {opt.label}
          </Button>
        ))}
      </div>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
