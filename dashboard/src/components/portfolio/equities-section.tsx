/**
 * equities-section.tsx — F2: header, grouping, donut, best/drags, equity table.
 */

"use client";

import * as React from "react";

import { BestGainsDrags } from "@/components/portfolio/best-gains-drags";
import {
  buildEquityGroups,
  EquityHoldingsTable,
  type EquityGroupMode,
} from "@/components/portfolio/equity-holdings-table";
import { AssetClassSectionHeadline } from "@/components/portfolio/asset-class-section-headline";
import { GroupingPieChart } from "@/components/portfolio/grouping-pie-chart";
import { GroupingToggle } from "@/components/portfolio/grouping-toggle";
import { Skeleton } from "@/components/ui/skeleton";
import { useHoldings, usePortfolioSummary } from "@/hooks/use-portfolio";
import type { Holding } from "@/lib/types";
export interface EquitiesSectionProps {
  userId: string;
}

type HoldingRow = Holding & { id: number };

const EQUITY_GROUP_OPTIONS: {
  value: EquityGroupMode;
  label: string;
}[] = [
  { value: "sector", label: "Sector" },
  { value: "market_cap", label: "Market cap" },
  { value: "holding_period", label: "Holding period" },
];

export function EquitiesSection({ userId }: EquitiesSectionProps) {
  const [groupMode, setGroupMode] = React.useState<EquityGroupMode>("sector");
  const { data: holdings, isLoading } = useHoldings({
    user_id: userId,
    asset_class: "EQUITY",
    is_active: true,
  });
  const { data: summary, isLoading: sLoad } = usePortfolioSummary({
    user_id: userId,
  });

  const rows = React.useMemo(
    () => (holdings ?? []).filter((h): h is HoldingRow => h.id != null),
    [holdings],
  );

  const eq = summary?.asset_class_breakdown?.EQUITY;

  const pieData = React.useMemo(() => {
    if (groupMode === "holding_period") {
      let lt = 0;
      let st = 0;
      let un = 0;
      for (const h of rows) {
        const p = h.equity_holding_period;
        if (!p) continue;
        lt += p.long_term_value_inr;
        st += p.short_term_value_inr;
        un += p.unallocated_value_inr;
      }
      const slices = [
        { name: "Long-term (>12 mo)", value: lt },
        { name: "Short-term (≤12 mo)", value: st },
      ];
      if (un > 0) {
        slices.push({
          name: "Unallocated (ledger gap / no buys)",
          value: un,
        });
      }
      return slices.filter((s) => s.value > 0);
    }
    const blocks = buildEquityGroups(rows, groupMode);
    return blocks.map((b) => ({ name: b.key, value: b.sumValue }));
  }, [rows, groupMode]);

  if (!isLoading && rows.length === 0) {
    return null;
  }

  return (
    <section
      id="holdings-section-equity"
      className="scroll-mt-24 space-y-4"
      aria-label="Equities"
    >
      <AssetClassSectionHeadline
        title="Equities"
        isLoading={sLoad}
        currentValue={eq?.current_value ?? 0}
        overallGain={eq?.overall_gain}
        overallGainPct={eq?.overall_gain_pct}
      />

      <GroupingToggle
        options={EQUITY_GROUP_OPTIONS}
        value={groupMode}
        onChange={setGroupMode}
        hint={
          groupMode === "holding_period"
            ? "Listed equity: long-term = held more than 12 calendar months from each buy (FIFO). Splits one script across LT and ST when you bought at different times."
            : undefined
        }
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <GroupingPieChart
          title="Equity mix"
          description="By selected grouping"
          data={pieData}
          isLoading={isLoading}
        />
        <div className="min-h-[120px]">
          {isLoading ? (
            <Skeleton className="h-full min-h-[120px] w-full" />
          ) : (
            <BestGainsDrags holdings={rows} />
          )}
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <EquityHoldingsTable holdings={rows} groupMode={groupMode} />
      )}
    </section>
  );
}
