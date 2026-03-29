/**
 * portfolio-value-headline.tsx — big total portfolio number + cumulative P&L (B3).
 * Daily change is intentionally omitted (holdings rebuild spec).
 */

"use client";

import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePortfolioSummary, useRefreshPrices } from "@/hooks/use-portfolio";
import {
  cn,
  formatCalendarDate,
  formatCurrency,
  formatPercent,
} from "@/lib/utils";

export interface PortfolioValueHeadlineProps {
  userId: string;
}

export function PortfolioValueHeadline({ userId }: PortfolioValueHeadlineProps) {
  const { data, isLoading } = usePortfolioSummary({ user_id: userId });
  const refresh = useRefreshPrices();

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-12 w-64" />
        <Skeleton className="h-4 w-48" />
      </div>
    );
  }

  const total = data?.total_portfolio_value ?? 0;
  const gain = data?.total_overall_gain;
  const gainPct = data?.total_overall_gain_pct;
  const asOf = data?.net_worth?.as_of;

  const gainPositive = gain != null && gain > 0;
  const gainNegative = gain != null && gain < 0;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">Current total portfolio value</p>
        <Button
          type="button"
          size="xs"
          variant="outline"
          className="gap-1"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate({ user_id: userId })}
        >
          <RefreshCw
            className={cn("size-3.5", refresh.isPending && "animate-spin")}
          />
          Refresh prices
        </Button>
      </div>
      <p className="text-3xl font-semibold tracking-tight sm:text-4xl">
        {formatCurrency(total)}
      </p>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        {gain != null && gainPct != null ? (
          <span
            className={cn(
              "font-medium",
              gainPositive && "text-emerald-600 dark:text-emerald-400",
              gainNegative && "text-red-600 dark:text-red-400",
              !gainPositive && !gainNegative && "text-muted-foreground",
            )}
          >
            {gainPositive ? "+" : ""}
            {formatCurrency(gain)} ({gainPositive ? "+" : ""}
            {formatPercent(gainPct, 1)}) overall
          </span>
        ) : (
          <span className="text-muted-foreground">
            Overall gain unavailable (cost basis missing on some rows).
          </span>
        )}
        <span className="text-muted-foreground">
          as on {formatCalendarDate(asOf ?? null)}
        </span>
      </div>
    </div>
  );
}
