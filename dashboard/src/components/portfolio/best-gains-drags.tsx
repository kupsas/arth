/**
 * best-gains-drags.tsx — top 3 / bottom 3 by **overall** gain % (not daily).
 */

"use client";

import * as React from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Holding } from "@/lib/types";
import { cn, formatPercent } from "@/lib/utils";

type HoldingRow = Holding & { id: number };

export interface BestGainsDragsProps {
  holdings: HoldingRow[];
}

export function BestGainsDrags({ holdings }: BestGainsDragsProps) {
  const { winners, losers } = React.useMemo(() => {
    const withPct = holdings.filter(
      (h) => h.overall_gain_pct != null && Number.isFinite(h.overall_gain_pct),
    );
    const pos = withPct
      .filter((h) => (h.overall_gain_pct as number) > 0)
      .sort((a, b) => (b.overall_gain_pct as number) - (a.overall_gain_pct as number))
      .slice(0, 3);
    const neg = withPct
      .filter((h) => (h.overall_gain_pct as number) < 0)
      .sort((a, b) => (a.overall_gain_pct as number) - (b.overall_gain_pct as number))
      .slice(0, 3);
    return { winners: pos, losers: neg };
  }, [holdings]);

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Best gains</CardTitle>
          <p className="text-xs text-muted-foreground">
            Highest overall gain % (positions in the green)
          </p>
        </CardHeader>
        <CardContent className="space-y-2">
          {winners.length === 0 ? (
            <p className="text-sm text-muted-foreground">No winners to show yet.</p>
          ) : (
            winners.map((h) => (
              <div
                key={h.id}
                className="flex items-center justify-between gap-2 text-sm"
              >
                <span className="font-medium truncate">
                  {h.symbol ?? h.name}
                </span>
                <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 shrink-0 tabular-nums">
                  <ArrowUp className="size-3.5" />
                  {formatPercent(h.overall_gain_pct as number, 1)}
                </span>
              </div>
            ))
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Biggest drags</CardTitle>
          <p className="text-xs text-muted-foreground">
            Worst overall gain % (positions underwater)
          </p>
        </CardHeader>
        <CardContent className="space-y-2">
          {losers.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing underwater with a known cost basis.
            </p>
          ) : (
            losers.map((h) => (
              <div
                key={h.id}
                className="flex items-center justify-between gap-2 text-sm"
              >
                <span className="font-medium truncate">
                  {h.symbol ?? h.name}
                </span>
                <span
                  className={cn(
                    "flex items-center gap-1 shrink-0 tabular-nums",
                    "text-red-600 dark:text-red-400",
                  )}
                >
                  <ArrowDown className="size-3.5" />
                  {formatPercent(h.overall_gain_pct as number, 1)}
                </span>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
