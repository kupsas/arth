/**
 * concentration-card.tsx — largest position + ESOP sleeve (F2.5.4).
 *
 * Data comes from ``holdings_summary.concentration`` (server-side weights).
 * We surface simple thresholds so a quick glance flags accidental over-allocation.
 */

"use client";

import { AlertTriangle } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHoldingsSummary } from "@/hooks/use-portfolio";
import { cn, formatPercent } from "@/lib/utils";

const SINGLE_NAME_WARN = 20;
const ESOP_WARN = 30;

export interface ConcentrationCardProps {
  userId: string;
}

export function ConcentrationCard({ userId }: ConcentrationCardProps) {
  const { data, isLoading } = useHoldingsSummary({ user_id: userId });
  const c = data?.concentration;

  const largestPct =
    typeof c?.largest_holding_pct === "number" ? c.largest_holding_pct : null;
  const largestName =
    typeof c?.largest_holding_name === "string" ? c.largest_holding_name : null;
  const esopPct = typeof c?.esop_pct === "number" ? c.esop_pct : null;

  const singleHot = largestPct != null && largestPct > SINGLE_NAME_WARN;
  const esopHot = esopPct != null && esopPct > ESOP_WARN;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Concentration</CardTitle>
        <p className="text-xs text-muted-foreground">
          How much of the portfolio sits in one name or in ESOPs
        </p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : (
          <>
            <div
              className={cn(
                "rounded-md border p-3",
                singleHot && "border-amber-500/40 bg-amber-500/5",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-muted-foreground text-xs">Largest holding</p>
                  <p className="font-medium">{largestName ?? "—"}</p>
                </div>
                <span className="tabular-nums font-semibold">
                  {largestPct != null ? formatPercent(largestPct) : "—"}
                </span>
              </div>
              {singleHot && (
                <p className="mt-2 flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="size-3.5 shrink-0" />
                  Above {SINGLE_NAME_WARN}% in a single line — review risk.
                </p>
              )}
            </div>

            <div
              className={cn(
                "rounded-md border p-3",
                esopHot && "border-amber-500/40 bg-amber-500/5",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-muted-foreground text-xs">ESOP exposure</p>
                <span className="tabular-nums font-semibold">
                  {esopPct != null ? formatPercent(esopPct) : "—"}
                </span>
              </div>
              {esopHot && (
                <p className="mt-2 flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="size-3.5 shrink-0" />
                  ESOPs over {ESOP_WARN}% — liquidity and tax planning matter.
                </p>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
