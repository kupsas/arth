/**
 * portfolio-summary-cards.tsx — top-level portfolio KPIs (F2.5.1).
 *
 * Pulls net worth and asset totals from GET /api/holdings/summary and liability
 * aggregates from GET /api/liabilities/summary. Both endpoints accept the same
 * ``user_id`` as your login username.
 */

"use client";

import * as React from "react";
import { Wallet, Landmark, Scale, TrendingUp } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHoldingsSummary, useLiabilitySummary } from "@/hooks/use-portfolio";
import { formatCurrency, formatPercent } from "@/lib/utils";

export interface PortfolioSummaryCardsProps {
  /** Logged-in username — passed through as API ``user_id``. */
  userId: string;
}

export function PortfolioSummaryCards({ userId }: PortfolioSummaryCardsProps) {
  const { data: summary, isLoading: sLoad } = useHoldingsSummary({ user_id: userId });
  const { data: liab, isLoading: lLoad } = useLiabilitySummary({ user_id: userId });

  const loading = sLoad || lLoad;

  const nw = summary?.net_worth;
  const debtToAsset = liab?.debt_to_asset_ratio ?? 0;

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        title="Net worth"
        icon={TrendingUp}
        loading={loading}
        value={nw ? formatCurrency(nw.net_worth) : "—"}
        hint="Assets minus liabilities (tracked layer)"
      />
      <MetricCard
        title="Total assets"
        icon={Wallet}
        loading={loading}
        value={nw ? formatCurrency(nw.total_assets) : "—"}
        hint="Sum of active holding marks"
      />
      <MetricCard
        title="Total liabilities"
        icon={Landmark}
        loading={loading}
        value={liab ? formatCurrency(liab.principal_outstanding) : "—"}
        hint="Outstanding principal on active loans"
      />
      <MetricCard
        title="Debt-to-asset"
        icon={Scale}
        loading={loading}
        value={liab ? formatPercent(debtToAsset) : "—"}
        hint="Lower is generally healthier"
      />
    </div>
  );
}

function MetricCard({
  title,
  icon: Icon,
  value,
  hint,
  loading,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  value: string;
  hint: string;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className="size-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-28" />
        ) : (
          <p className="text-2xl font-semibold tabular-nums">{value}</p>
        )}
        <p className="text-xs text-muted-foreground mt-1">{hint}</p>
      </CardContent>
    </Card>
  );
}
