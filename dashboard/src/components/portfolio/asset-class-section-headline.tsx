/**
 * asset-class-section-headline.tsx — section title + value + P&L for one sleeve
 * (equities, mutual funds). Mirrors portfolio-value-headline layout but smaller
 * type (H2-ish vs the page headline H1).
 */

"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";

export interface AssetClassSectionHeadlineProps {
  /** e.g. "Equities", "Mutual funds" */
  title: string;
  isLoading?: boolean;
  currentValue: number;
  overallGain: number | null | undefined;
  overallGainPct: number | null | undefined;
  /** Shown when gain cannot be computed (missing cost on rows). */
  gainUnavailableMessage?: string;
}

function gainClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-emerald-600 dark:text-emerald-400";
  if (v < 0) return "text-red-600 dark:text-red-400";
  return "text-muted-foreground";
}

export function AssetClassSectionHeadline({
  title,
  isLoading,
  currentValue,
  overallGain,
  overallGainPct,
  gainUnavailableMessage = "Sub-portfolio gain needs cost on each row.",
}: AssetClassSectionHeadlineProps) {
  return (
    <div className="space-y-1.5">
      <p className="text-sm text-muted-foreground">{title}</p>
      {isLoading ? (
        <>
          <Skeleton className="h-9 w-52 max-w-full sm:h-10" />
          <Skeleton className="h-4 w-64 max-w-full" />
        </>
      ) : (
        <>
          {/* Headline uses text-3xl/4xl; this is one step down (H2-style). */}
          <p className="text-2xl font-semibold tracking-tight sm:text-3xl">
            {formatCurrency(currentValue)}
          </p>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
            {overallGain != null && overallGainPct != null ? (
              <span
                className={cn(
                  "font-medium",
                  gainClass(overallGain),
                )}
              >
                {overallGain > 0 ? "+" : ""}
                {formatCurrency(overallGain)} ({overallGainPct > 0 ? "+" : ""}
                {formatPercent(overallGainPct, 1)}) overall
              </span>
            ) : (
              <span className="text-muted-foreground">
                {gainUnavailableMessage}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
