/**
 * mutual-funds-section.tsx — F3: MF header, category mix donut + holdings table + batch XIRR.
 * Grouping is always fund category (pie and table stay aligned).
 */

"use client";

import * as React from "react";

import { AssetClassSectionHeadline } from "@/components/portfolio/asset-class-section-headline";
import { GroupingPieChart } from "@/components/portfolio/grouping-pie-chart";
import { buildMfGroups, MfHoldingsTable } from "@/components/portfolio/mf-holdings-table";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useBatchReturns,
  useHoldings,
  usePortfolioSummary,
} from "@/hooks/use-portfolio";
import type { Holding } from "@/lib/types";
export interface MutualFundsSectionProps {
  userId: string;
}

type HoldingRow = Holding & { id: number };

export function MutualFundsSection({ userId }: MutualFundsSectionProps) {
  const { data: holdings, isLoading } = useHoldings({
    user_id: userId,
    asset_class: "MUTUAL_FUND",
    is_active: true,
  });
  const { data: summary, isLoading: sLoad } = usePortfolioSummary({
    user_id: userId,
  });
  const { data: batchRet, isLoading: rLoad } = useBatchReturns({
    user_id: userId,
  });

  const rows = React.useMemo(
    () => (holdings ?? []).filter((h): h is HoldingRow => h.id != null),
    [holdings],
  );

  const mf = summary?.asset_class_breakdown?.MUTUAL_FUND;

  const returnsMap = batchRet?.returns ?? {};

  const pieData = React.useMemo(() => {
    const blocks = buildMfGroups(rows);
    return blocks.map((b) => ({ name: b.key, value: b.sumValue }));
  }, [rows]);

  if (!isLoading && rows.length === 0) {
    return null;
  }

  return (
    <section
      id="holdings-section-mf"
      className="scroll-mt-24 space-y-4"
      aria-label="Mutual funds"
    >
      <AssetClassSectionHeadline
        title="Mutual funds"
        isLoading={sLoad}
        currentValue={mf?.current_value ?? 0}
        overallGain={mf?.overall_gain}
        overallGainPct={mf?.overall_gain_pct}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(280px,360px)_1fr] lg:items-start">
        <GroupingPieChart
          title="Mutual fund mix"
          description="By fund category"
          data={pieData}
          isLoading={isLoading}
          className="h-full min-w-0"
          legendLayout="below"
        />
        <div className="min-w-0">
          {isLoading || rLoad ? (
            <Skeleton className="h-96 min-h-48 w-full" />
          ) : (
            <MfHoldingsTable
              holdings={rows}
              returnsByHoldingId={returnsMap}
            />
          )}
        </div>
      </div>
    </section>
  );
}
